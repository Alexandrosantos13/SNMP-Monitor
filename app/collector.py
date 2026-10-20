"""Coletor SNMP: thread única, com Iniciar / Pausar / Resetar e cálculo de taxas (bps)."""
import threading
import time
from collections import deque

from .snmp_client import SimClient, SnmpClient, SnmpError

DEFAULT_MAX_BPS = 10e9  # limite de plausibilidade quando a velocidade da interface é desconhecida


def rate_bps(prev, cur, dt, bits=64, max_bps=None):
    """Taxa em bps = (delta de bytes * 8) / intervalo real (s).
    Retorna None (amostra descartada) se o contador reiniciou, dt inválido ou a taxa é absurda."""
    if dt is None or dt <= 0:
        return None
    if cur >= prev:
        delta = cur - prev
    elif bits == 32:
        delta = cur + 2 ** 32 - prev          # wrap-around normal de Counter32
    else:
        return None                           # Counter64 diminuiu: contador reiniciado
    bps = delta * 8 / dt
    if max_bps and bps > max_bps * 1.1:
        return None                           # salto impossível (reset aparente de Counter32 etc.)
    return bps


class Collector:
    def __init__(self, storage, alerts, get_cfg):
        self.storage, self.alerts, self.get_cfg = storage, alerts, get_cfg
        self._lock = threading.Lock()
        self._thread = None
        self._running = threading.Event()   # set = coletando; clear = pausado
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.buffer = deque(maxlen=720)     # série exibida no tempo real
        self.session, self._seq = 1, 0
        self._client = self._client_key = None
        self._reset_baseline()
        self._ifindex = self._ifname = self._speed = None
        self._hc = True
        self.status = {"last_error": None, "last_ok": None, "polls": 0, "errors": 0, "cpu_supported": None}

    # ---------- controles ----------
    @property
    def state(self):
        if self._thread and self._thread.is_alive() and self._running.is_set():
            return "running"
        return "paused" if self.buffer or self._thread else "stopped"

    def start(self):
        """Inicia ou retoma. Nunca cria uma segunda thread de coleta."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name="snmp-collector", daemon=True)
                self._running.set()
                self._thread.start()
            else:
                self._running.set()
            self._reset_baseline()          # evita calcular taxa sobre o intervalo da pausa
            self._wake.set()

    def pause(self):
        self._running.clear()

    def reset(self):
        """Limpa a série do tempo real. O histórico no banco NÃO é apagado."""
        with self._lock:
            self.buffer.clear()
            self.session += 1
            self._reset_baseline()
            self.alerts.rearm()

    def shutdown(self):
        self._stop.set()
        self._wake.set()

    def _reset_baseline(self):
        self._prev = None

    # ---------- laço ----------
    def _loop(self):
        while not self._stop.is_set():
            if not self._running.is_set():
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            t0 = time.monotonic()
            cfg = self.get_cfg()
            try:
                self._cycle(cfg)
            except SnmpError as exc:
                self._on_error(str(exc))
            except Exception as exc:  # nunca deixa a thread morrer
                self._on_error(f"Erro inesperado na coleta: {exc!r}")
            wait = max(0.2, float(cfg["collect_interval"]) - (time.monotonic() - t0))
            self._wake.wait(wait)
            self._wake.clear()
        self._drop_client()

    def _drop_client(self):
        if self._client:
            self._client.close()
        self._client = self._client_key = None
        self._ifindex = None

    def _ensure_client(self, cfg):
        key = (repr(cfg["snmp"]), cfg["simulate"])
        if key != self._client_key:
            self._drop_client()
            self._client = SimClient(cfg["snmp"]) if cfg["simulate"] else SnmpClient(cfg["snmp"])
            self._client_key = key
            self._reset_baseline()
        if self._ifindex is None:
            self._ifindex, self._ifname, self._speed = self._client.resolve_interface(cfg["snmp"]["interface"])
            self._hc = True
            self._reset_baseline()
        return self._client

    def _cycle(self, cfg):
        client = self._ensure_client(cfg)
        data = client.poll(self._ifindex, self._hc)
        t_mono, ts = time.monotonic(), time.time()
        self._hc = data["counter_bits"] == 64

        if data["ifname"] and data["ifname"] != self._ifname:   # índice passou a apontar para outra interface
            self.storage.add_event(ts, "iface_changed", "warning",
                                   f"Interface {self._ifname} mudou para {data['ifname']}; reconsultando índice.")
            self._ifindex = None
            self._reset_baseline()
            raise SnmpError("Interface monitorada mudou de índice; reconfigurando na próxima coleta.")

        in_bps = out_bps = None
        prev = self._prev
        if prev and data["uptime"] < prev["uptime"]:
            self.storage.add_event(ts, "device_reboot", "warning", "Roteador reiniciado (sysUpTime diminuiu); baseline reiniciada.")
            prev = None
        if prev:
            dt = t_mono - prev["t"]
            cap = self._speed or DEFAULT_MAX_BPS
            in_bps = rate_bps(prev["in"], data["in_octets"], dt, data["counter_bits"], cap)
            out_bps = rate_bps(prev["out"], data["out_octets"], dt, data["counter_bits"], cap)
            if in_bps is None or out_bps is None:
                self.storage.add_event(ts, "counter_reset", "warning", "Contador reiniciado ou taxa inválida; amostra de tráfego descartada.")
        self._prev = {"t": t_mono, "in": data["in_octets"], "out": data["out_octets"], "uptime": data["uptime"]}

        cpu = data["cpu"]
        if self.status["cpu_supported"] is not False and not data["cpu_supported"]:
            self.storage.add_event(ts, "cpu_unsupported", "warning", "Equipamento não retornou hrProcessorLoad (CPU indisponível).")
        self.status["cpu_supported"] = data["cpu_supported"]

        self.storage.add_sample(ts, in_bps, out_bps, cpu)
        with self._lock:
            self._seq += 1
            self.buffer.append({"seq": self._seq, "ts": ts, "in_bps": in_bps, "out_bps": out_bps, "cpu": cpu})
        self.alerts.evaluate(ts, in_bps, out_bps, cpu)

        if self.status["last_error"]:
            self.storage.add_event(ts, "recovered_comm", "info", "Comunicação SNMP restabelecida.")
        self.status.update(last_error=None, last_ok=ts, polls=self.status["polls"] + 1,
                           ifname=self._ifname, counter_bits=data["counter_bits"])

    def _on_error(self, msg):
        now = time.time()
        if msg != self.status["last_error"]:       # registra só quando a mensagem muda (sem inundar o log)
            self.storage.add_event(now, "snmp_error", "error", msg)
        self.status.update(last_error=msg, errors=self.status["errors"] + 1)
        self._reset_baseline()

    # ---------- consulta ----------
    def live(self, since, session):
        with self._lock:
            if session != self.session:
                since = 0
            samples = [s for s in self.buffer if s["seq"] > since]
            return {"session": self.session, "samples": samples}
