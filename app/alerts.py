"""Avaliação de thresholds e envio de notificações (pop-up na interface e e-mail).

Estratégia anti-repetição (por métrica: IN, OUT, CPU):
  * notifica na transição normal -> excedido;
  * enquanto continuar excedido, só repete após `cooldown_s` segundos (0 = nunca repete);
  * ao voltar ao normal o estado é rearmado e a recuperação é registrada no log de eventos.
"""
import smtplib
import ssl
import threading
import time
from collections import deque
from datetime import datetime
from email.message import EmailMessage


def fmt_bps(v):
    for unit, div in (("Gbps", 1e9), ("Mbps", 1e6), ("kbps", 1e3)):
        if v >= div:
            return f"{v / div:.2f} {unit}"
    return f"{v:.0f} bps"


def fmt_value(metric, v):
    return f"{v:.1f}%" if metric == "cpu" else f"{fmt_bps(v)} ({v:.0f} bps)"


def fmt_limit(metric, v):
    return f"{v:g}%" if metric == "cpu" else f"{fmt_bps(v)} ({v:.0f} bps)"


LABELS = {"in": "Tráfego de entrada (IN)", "out": "Tráfego de saída (OUT)", "cpu": "Utilização da CPU"}
KEYS = {"in": "threshold_in_bps", "out": "threshold_out_bps", "cpu": "threshold_cpu"}


def send_email(cfg, subject, body):
    """Envia e-mail via SMTP. Lança exceção em caso de falha."""
    e = cfg["email"]
    if not e["to"]:
        raise ValueError("e-mail de destino não configurado")
    if not e["smtp_host"]:
        raise ValueError("servidor SMTP não configurado")
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, e["to"]
    msg["From"] = e["from"] or e["smtp_user"] or "monitor@localhost"
    msg.set_content(body)
    host, port = e["smtp_host"], int(e["smtp_port"])
    if e["security"] == "ssl":
        smtp = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
    else:
        smtp = smtplib.SMTP(host, port, timeout=15)
    with smtp:
        if e["security"] == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if e["smtp_user"]:
            smtp.login(e["smtp_user"], e["smtp_password"])
        smtp.send_message(msg)


class AlertEngine:
    def __init__(self, storage, get_cfg):
        self.storage, self.get_cfg = storage, get_cfg
        self._state = {k: {"exceeded": False, "last": 0.0} for k in KEYS}
        self._feed = deque(maxlen=200)   # itens exibidos na interface (pop-up / avisos)
        self._seq = 0
        self._lock = threading.Lock()

    # ---- fila de notificações para a interface ----
    def _push(self, **item):
        with self._lock:
            self._seq += 1
            item.update(id=self._seq, ts=time.time())
            self._feed.append(item)

    def feed_since(self, last_id):
        with self._lock:
            return [i for i in self._feed if i["id"] > last_id]

    def rearm(self):
        for st in self._state.values():
            st["exceeded"], st["last"] = False, 0.0

    # ---- avaliação ----
    def evaluate(self, ts, in_bps, out_bps, cpu):
        cfg = self.get_cfg()
        a = cfg["alerts"]
        for metric, value in (("in", in_bps), ("out", out_bps), ("cpu", cpu)):
            limit = a[KEYS[metric]]
            st = self._state[metric]
            if value is None or not limit or limit <= 0:
                st["exceeded"] = False
                continue
            if value > limit:
                due = (not st["exceeded"]) or (a["cooldown_s"] > 0 and ts - st["last"] >= a["cooldown_s"])
                st["exceeded"] = True
                if due:
                    st["last"] = ts
                    self._fire(cfg, metric, value, limit, ts)
            elif st["exceeded"]:
                st["exceeded"] = False
                self.storage.add_event(ts, "recovered", "info",
                                       f"{LABELS[metric]} voltou ao normal: {fmt_value(metric, value)} "
                                       f"(limite {fmt_limit(metric, limit)}).")

    def _fire(self, cfg, metric, value, limit, ts):
        a = cfg["alerts"]
        when = datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M:%S")
        text = (f"{LABELS[metric]} acima do limite: observado {fmt_value(metric, value)}, "
                f"limite {fmt_limit(metric, limit)}, em {when}.")
        channels = [c for c, on in (("popup", a["popup"]), ("e-mail", a["email"])) if on]
        self.storage.add_event(ts, "alert", "warning",
                               text + (f" [canais: {', '.join(channels)}]" if channels else " [nenhum canal ativo]"))
        if a["popup"]:
            self._push(kind="alert", metric=metric, label=LABELS[metric], value=fmt_value(metric, value),
                       limit=fmt_limit(metric, limit), when=when, text=text)
        if a["email"]:
            threading.Thread(target=self._email_worker, args=(cfg, metric, text, when), daemon=True).start()

    def _email_worker(self, cfg, metric, text, when):
        subject = f"[Monitor SNMP] Alerta: {LABELS[metric]}"
        body = (f"{text}\n\nRoteador: {cfg['snmp']['host']}  Interface: {cfg['snmp']['interface']}\n"
                f"Horário: {when}\n")
        try:
            send_email(cfg, subject, body)
            self.storage.add_event(time.time(), "email_ok", "info", f"E-mail de alerta enviado para {cfg['email']['to']}.")
        except Exception as exc:  # qualquer falha de rede/SMTP não derruba a aplicação
            msg = f"Falha ao enviar e-mail de alerta: {exc}"
            self.storage.add_event(time.time(), "email_error", "error", msg)
            self._push(kind="notice", level="error", text=msg)
