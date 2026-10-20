"""Carregamento, validação e gravação das configurações (config.json)."""
import copy
import json
import os
import re
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get("MONITOR_CONFIG", os.path.join(BASE_DIR, "config.json"))

DEFAULTS = {
    "snmp": {
        "host": "192.168.88.1",
        "port": 161,
        "version": "2c",            # "2c" ou "3"
        "community": "public",      # v2c
        "v3_user": "",              # v3
        "v3_auth_protocol": "SHA",  # MD5 | SHA | SHA224 | SHA256 | SHA384 | SHA512 | NONE
        "v3_auth_password": "",
        "v3_priv_protocol": "AES",  # DES | AES | AES192 | AES256 | NONE
        "v3_priv_password": "",
        "interface": "ether1",      # nome (ifName) ou índice numérico
        "timeout": 2,
        "retries": 1,
    },
    "collect_interval": 5,          # segundos
    "alerts": {
        "popup": True,
        "email": False,
        "threshold_in_bps": 0,      # 0 = desativado
        "threshold_out_bps": 0,
        "threshold_cpu": 0,
        "cooldown_s": 300,          # novo alerta enquanto excedido (lembrete)
    },
    "email": {
        "to": "",
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "security": "starttls",     # starttls | ssl | none
        "smtp_user": "",
        "smtp_password": "",        # ou variável de ambiente SMTP_PASSWORD
        "from": "",
    },
    "simulate": False,              # dados sintéticos, sem roteador (apenas desenvolvimento)
}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
SECRET_FIELDS = [("snmp", "community"), ("snmp", "v3_auth_password"),
                 ("snmp", "v3_priv_password"), ("email", "smtp_password")]
_lock = threading.Lock()


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def load():
    cfg = copy.deepcopy(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = _merge(DEFAULTS, json.load(f))
        except (OSError, ValueError):
            pass  # arquivo corrompido: usa padrões
    if os.environ.get("SMTP_PASSWORD"):
        cfg["email"]["smtp_password"] = os.environ["SMTP_PASSWORD"]
    return cfg


def save(cfg):
    with _lock:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        os.replace(tmp, CONFIG_PATH)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass


def _num(v, name, errors, minimum=None, integer=False, allow_zero=True):
    try:
        n = float(v)
    except (TypeError, ValueError):
        errors.append(f"{name}: valor numérico inválido.")
        return None
    if integer:
        n = int(n)
    if minimum is not None and n < minimum:
        errors.append(f"{name}: deve ser maior ou igual a {minimum}.")
    if not allow_zero and n == 0:
        errors.append(f"{name}: não pode ser zero.")
    return n


def validate(new, current):
    """Valida o dicionário vindo da interface. Retorna (cfg, erros).
    Segredos vazios/mascarados mantêm o valor atual."""
    errors = []
    cfg = _merge(current, new)
    for sec, key in SECRET_FIELDS:
        incoming = (new.get(sec) or {}).get(key) if sec else new.get(key)
        if incoming in (None, "", "********"):
            cfg[sec][key] = current[sec][key]

    s, a, e = cfg["snmp"], cfg["alerts"], cfg["email"]

    # Intervalo de coleta: rejeita zero e negativos
    cfg["collect_interval"] = _num(cfg["collect_interval"], "Intervalo de coleta", errors)
    if cfg["collect_interval"] is not None and cfg["collect_interval"] <= 0:
        errors.append("Intervalo de coleta: deve ser maior que zero.")

    if not str(s["host"]).strip():
        errors.append("Endereço do roteador é obrigatório.")
    s["host"] = str(s["host"]).strip()
    s["port"] = _num(s["port"], "Porta SNMP", errors, 1, True)
    s["timeout"] = _num(s["timeout"], "Timeout", errors, 0.5)
    s["retries"] = _num(s["retries"], "Tentativas", errors, 0, True)
    if s["version"] not in ("2c", "3"):
        errors.append("Versão SNMP deve ser 2c ou 3.")
    if s["version"] == "2c" and not s["community"]:
        errors.append("Community SNMP é obrigatória na versão 2c.")
    if s["version"] == "3" and not s["v3_user"]:
        errors.append("Usuário SNMPv3 é obrigatório.")
    if not str(s["interface"]).strip():
        errors.append("Interface monitorada é obrigatória.")
    s["interface"] = str(s["interface"]).strip()

    for k, label in (("threshold_in_bps", "Threshold de tráfego IN"),
                     ("threshold_out_bps", "Threshold de tráfego OUT"),
                     ("threshold_cpu", "Threshold de CPU"),
                     ("cooldown_s", "Intervalo entre alertas repetidos")):
        a[k] = _num(a[k], label, errors, 0)
    if a["threshold_cpu"] is not None and a["threshold_cpu"] > 100:
        errors.append("Threshold de CPU: máximo 100%.")
    a["popup"], a["email"] = bool(a["popup"]), bool(a["email"])

    e["to"] = str(e["to"]).strip()
    if e["to"] and not EMAIL_RE.match(e["to"]):
        errors.append("Endereço de e-mail com formato inválido.")
    if a["email"] and not e["to"]:
        errors.append("Informe o e-mail de destino para usar o alerta por e-mail.")
    e["smtp_port"] = _num(e["smtp_port"], "Porta SMTP", errors, 1, True)
    if e["security"] not in ("starttls", "ssl", "none"):
        errors.append("Segurança SMTP inválida.")
    cfg["simulate"] = bool(cfg.get("simulate"))
    return cfg, errors


def public_view(cfg):
    """Cópia para a interface, com segredos mascarados."""
    out = copy.deepcopy(cfg)
    for sec, key in SECRET_FIELDS:
        out[sec][key] = "********" if cfg[sec][key] else ""
    return out
