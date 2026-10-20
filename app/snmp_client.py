"""Cliente SNMP (pysnmp >= 7) e simulador de dados para desenvolvimento.

OIDs utilizados (todos de MIBs padrão, suportadas pelo RouterOS/MikroTik):
  IF-MIB::ifName            1.3.6.1.2.1.31.1.1.1.1.<idx>   nome da interface (ex.: ether1)
  IF-MIB::ifDescr           1.3.6.1.2.1.2.2.1.2.<idx>      (alternativa para localizar a interface)
  IF-MIB::ifHCInOctets      1.3.6.1.2.1.31.1.1.1.6.<idx>   bytes recebidos (Counter64)
  IF-MIB::ifHCOutOctets     1.3.6.1.2.1.31.1.1.1.10.<idx>  bytes enviados  (Counter64)
  IF-MIB::ifHighSpeed       1.3.6.1.2.1.31.1.1.1.15.<idx>  velocidade nominal (Mbps)
  IF-MIB::ifInOctets        1.3.6.1.2.1.2.2.1.10.<idx>     (Counter32, fallback)
  IF-MIB::ifOutOctets       1.3.6.1.2.1.2.2.1.16.<idx>     (Counter32, fallback)
  SNMPv2-MIB::sysUpTime     1.3.6.1.2.1.1.3.0              detecta reinicialização do equipamento
  HOST-RESOURCES-MIB::hrProcessorLoad 1.3.6.1.2.1.25.3.3.1.2.<n>  carga (%) de cada núcleo
"""
import asyncio
import math
import time

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData, ContextData, ObjectIdentity, ObjectType, SnmpEngine,
    UdpTransportTarget, UsmUserData, get_cmd, walk_cmd,
    usmHMACMD5AuthProtocol, usmHMACSHAAuthProtocol, usmHMAC128SHA224AuthProtocol,
    usmHMAC192SHA256AuthProtocol, usmHMAC256SHA384AuthProtocol, usmHMAC384SHA512AuthProtocol,
    usmNoAuthProtocol, usmDESPrivProtocol, usmAesCfb128Protocol, usmAesCfb192Protocol,
    usmAesCfb256Protocol, usmNoPrivProtocol,
)

OID_IFNAME = "1.3.6.1.2.1.31.1.1.1.1"
OID_IFDESCR = "1.3.6.1.2.1.2.2.1.2"
OID_IFHCIN = "1.3.6.1.2.1.31.1.1.1.6"
OID_IFHCOUT = "1.3.6.1.2.1.31.1.1.1.10"
OID_IFHIGHSPEED = "1.3.6.1.2.1.31.1.1.1.15"
OID_IFIN32 = "1.3.6.1.2.1.2.2.1.10"
OID_IFOUT32 = "1.3.6.1.2.1.2.2.1.16"
OID_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_CPU = "1.3.6.1.2.1.25.3.3.1.2"

AUTH = {"MD5": usmHMACMD5AuthProtocol, "SHA": usmHMACSHAAuthProtocol,
        "SHA224": usmHMAC128SHA224AuthProtocol, "SHA256": usmHMAC192SHA256AuthProtocol,
        "SHA384": usmHMAC256SHA384AuthProtocol, "SHA512": usmHMAC384SHA512AuthProtocol,
        "NONE": usmNoAuthProtocol}
PRIV = {"DES": usmDESPrivProtocol, "AES": usmAesCfb128Protocol, "AES192": usmAesCfb192Protocol,
        "AES256": usmAesCfb256Protocol, "NONE": usmNoPrivProtocol}


class SnmpError(Exception):
    """Falha de consulta SNMP (timeout, autenticação, OID ausente...)."""


def _missing(val):
    n = type(val).__name__
    return n.startswith("NoSuch") or n == "EndOfMibView"


class SnmpClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.loop = asyncio.new_event_loop()
        self.engine = SnmpEngine()
        self._target = None
        self._auth = self._build_auth()

    # ---------- infraestrutura ----------
    def _build_auth(self):
        c = self.cfg
        if c["version"] == "2c":
            return CommunityData(c["community"], mpModel=1)
        auth_p, priv_p = c["v3_auth_protocol"].upper(), c["v3_priv_protocol"].upper()
        kw = {}
        if auth_p != "NONE":
            kw.update(authKey=c["v3_auth_password"], authProtocol=AUTH[auth_p])
        if priv_p != "NONE" and auth_p != "NONE":
            kw.update(privKey=c["v3_priv_password"], privProtocol=PRIV[priv_p])
        return UsmUserData(c["v3_user"], **kw)

    async def _get_target(self):
        if self._target is None:
            try:
                self._target = await UdpTransportTarget.create(
                    (self.cfg["host"], int(self.cfg["port"])),
                    timeout=float(self.cfg["timeout"]), retries=int(self.cfg["retries"]))
            except Exception as exc:  # DNS inválido etc.
                raise SnmpError(f"Endereço do roteador inválido: {exc}") from exc
        return self._target

    @staticmethod
    def _check(err_ind, err_stat, err_idx, var_binds):
        if err_ind:
            msg = str(err_ind)
            if "timeout" in msg.lower() or "no snmp response" in msg.lower():
                raise SnmpError("Sem resposta do roteador (timeout / sem conectividade).")
            if any(k in msg.lower() for k in ("wrong", "authentication", "unknown usm", "unknownusername", "unsupported security")):
                raise SnmpError(f"Falha de autenticação SNMPv3: {msg}")
            raise SnmpError(f"Erro SNMP: {msg}")
        if err_stat:
            where = var_binds[int(err_idx) - 1][0] if err_idx and var_binds else "?"
            raise SnmpError(f"Erro SNMP: {err_stat.prettyPrint()} em {where}")

    async def _get(self, oids):
        target = await self._get_target()
        res = await get_cmd(self.engine, self._auth, target, ContextData(),
                            *[ObjectType(ObjectIdentity(o)) for o in oids])
        self._check(*res)
        return {str(vb[0]): vb[1] for vb in res[3]}

    async def _walk(self, oid):
        target = await self._get_target()
        out = {}
        async for err_ind, err_stat, err_idx, var_binds in walk_cmd(
                self.engine, self._auth, target, ContextData(),
                ObjectType(ObjectIdentity(oid)), lexicographicMode=False):
            self._check(err_ind, err_stat, err_idx, var_binds)
            for name, val in var_binds:
                if not _missing(val):
                    out[str(name)] = val
        return out

    def _run(self, coro):
        try:
            return self.loop.run_until_complete(coro)
        except SnmpError:
            raise
        except Exception as exc:
            raise SnmpError(f"Erro SNMP: {exc}") from exc

    # ---------- operações ----------
    def resolve_interface(self, wanted):
        """Retorna (ifIndex, ifName, velocidade_bps|None). `wanted` = nome ou índice."""
        wanted = str(wanted).strip()
        names = self._run(self._walk(OID_IFNAME))
        if not names:
            names = self._run(self._walk(OID_IFDESCR))
        if not names:
            raise SnmpError("Roteador não retornou a tabela de interfaces (IF-MIB).")
        table = {int(k.rsplit(".", 1)[1]): str(v) for k, v in names.items()}
        idx = None
        for i, n in table.items():
            if n == wanted:
                idx = i
        if idx is None and wanted.isdigit() and int(wanted) in table:
            idx = int(wanted)
        if idx is None:
            avail = ", ".join(f"{n}" for n in table.values())
            raise SnmpError(f"Interface '{wanted}' não encontrada. Disponíveis: {avail}")
        speed = None
        try:
            r = self._run(self._get([f"{OID_IFHIGHSPEED}.{idx}"]))
            v = next(iter(r.values()))
            if not _missing(v) and int(v) > 0:
                speed = int(v) * 1_000_000
        except SnmpError:
            pass
        return idx, table[idx], speed

    def list_interfaces(self):
        names = self._run(self._walk(OID_IFNAME)) or self._run(self._walk(OID_IFDESCR))
        return sorted(((int(k.rsplit(".", 1)[1]), str(v)) for k, v in names.items()))

    def poll(self, ifindex, use_hc=True):
        """Uma leitura: contadores, uptime, nome da interface e CPU média.
        Retorna dict com in_octets/out_octets/counter_bits/uptime/ifname/cpu."""
        hc = use_hc
        in_o, out_o = (OID_IFHCIN, OID_IFHCOUT) if hc else (OID_IFIN32, OID_IFOUT32)
        want = [f"{in_o}.{ifindex}", f"{out_o}.{ifindex}", OID_UPTIME, f"{OID_IFNAME}.{ifindex}"]
        vals = self._run(self._get(want))
        if hc and any(_missing(vals[w]) for w in want[:2]):
            hc = False  # equipamento sem contadores de 64 bits: usa os de 32 bits
            want[0], want[1] = f"{OID_IFIN32}.{ifindex}", f"{OID_IFOUT32}.{ifindex}"
            vals = self._run(self._get(want))
        if any(_missing(vals[w]) for w in want[:3]):
            raise SnmpError("OID de contadores de tráfego não suportado pelo equipamento.")
        cpu = None
        try:
            loads = [int(v) for v in self._run(self._walk(OID_CPU)).values()]
            if loads:
                cpu = sum(loads) / len(loads)
        except SnmpError as exc:
            if "timeout" in str(exc).lower() or "Sem resposta" in str(exc):
                raise
        return {"in_octets": int(vals[want[0]]), "out_octets": int(vals[want[1]]),
                "counter_bits": 64 if hc else 32, "uptime": int(vals[want[2]]),
                "ifname": None if _missing(vals[want[3]]) else str(vals[want[3]]),
                "cpu": cpu, "cpu_supported": cpu is not None}

    def close(self):
        try:
            self.engine.close_dispatcher()
        except Exception:
            pass
        try:
            self.loop.close()
        except Exception:
            pass


class SimClient:
    """Gera contadores/CPU sintéticos com a mesma interface do SnmpClient."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.t0 = time.time()
        self.in_o = self.out_o = 0

    def resolve_interface(self, wanted):
        return 1, str(wanted), 1_000_000_000

    def list_interfaces(self):
        return [(1, "ether1"), (2, "ether2")]

    def poll(self, ifindex, use_hc=True):
        t = time.time() - self.t0
        in_bps = 40e6 + 35e6 * math.sin(t / 25) + 20e6 * math.sin(t / 7)
        out_bps = 12e6 + 8e6 * math.sin(t / 31 + 1) + 6e6 * math.sin(t / 5)
        self.in_o += int(max(in_bps, 0) / 8 * 1)
        self.out_o += int(max(out_bps, 0) / 8 * 1)
        # contadores avançam conforme o tempo real entre leituras
        now = time.time()
        dt = now - getattr(self, "_last", now - 1)
        self._last = now
        self.in_o += int(max(in_bps, 0) / 8 * (dt - 1))
        self.out_o += int(max(out_bps, 0) / 8 * (dt - 1))
        cpu = min(100, max(0, 25 + 20 * math.sin(t / 40) + 8 * math.sin(t / 6)))
        return {"in_octets": self.in_o, "out_octets": self.out_o, "counter_bits": 64,
                "uptime": int(t * 100), "ifname": self.cfg["interface"],
                "cpu": cpu, "cpu_supported": True}

    def close(self):
        pass
