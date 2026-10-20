"""API HTTP (Flask) e entrega da interface web."""
import atexit
import os
import time

from flask import Flask, jsonify, request, send_from_directory

from . import config as cfgmod
from .alerts import AlertEngine, send_email
from .collector import Collector
from .snmp_client import SimClient, SnmpClient, SnmpError
from .storage import Storage

STATIC = os.path.join(cfgmod.BASE_DIR, "static")


def create_app():
    app = Flask(__name__, static_folder=STATIC, static_url_path="/static")
    state = {"cfg": cfgmod.load()}
    storage = Storage()
    get_cfg = lambda: state["cfg"]
    alerts = AlertEngine(storage, get_cfg)
    collector = Collector(storage, alerts, get_cfg)
    atexit.register(collector.shutdown)

    def status():
        c = collector
        return {"state": c.state, "interval": state["cfg"]["collect_interval"],
                "interface": c.status.get("ifname") or state["cfg"]["snmp"]["interface"],
                "router": state["cfg"]["snmp"]["host"], "simulate": state["cfg"]["simulate"],
                "last_error": c.status["last_error"], "last_ok": c.status["last_ok"],
                "polls": c.status["polls"], "errors": c.status["errors"],
                "counter_bits": c.status.get("counter_bits"), "cpu_supported": c.status["cpu_supported"],
                "thresholds": {k: state["cfg"]["alerts"][k] for k in
                               ("threshold_in_bps", "threshold_out_bps", "threshold_cpu")}}

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/api/live")
    def live():
        since = request.args.get("since", 0, type=int)
        session = request.args.get("session", 0, type=int)
        last_alert = request.args.get("alert", 0, type=int)
        out = collector.live(since, session)
        out.update(status=status(), alerts=alerts.feed_since(last_alert), now=time.time())
        return jsonify(out)

    @app.post("/api/start")
    def start():
        collector.start()          # idempotente: não duplica a thread
        return jsonify(status())

    @app.post("/api/pause")
    def pause():
        collector.pause()
        return jsonify(status())

    @app.post("/api/reset")
    def reset():
        collector.reset()          # só a série em memória; histórico preservado
        return jsonify(status())

    @app.get("/api/history")
    def history():
        try:
            start_s, end_s = float(request.args["start"]), float(request.args["end"])
        except (KeyError, ValueError):
            return jsonify(error="Informe início e fim do período."), 400
        if end_s <= start_s:
            return jsonify(error="O fim do período deve ser posterior ao início."), 400
        return jsonify(storage.history(start_s, end_s))

    @app.get("/api/events")
    def events():
        return jsonify(storage.events(request.args.get("limit", 100, type=int)))

    @app.get("/api/config")
    def get_config():
        return jsonify(cfgmod.public_view(state["cfg"]))

    @app.post("/api/config")
    def set_config():
        new = request.get_json(silent=True) or {}
        cfg, errors = cfgmod.validate(new, state["cfg"])
        if errors:
            return jsonify(errors=errors), 400
        cfgmod.save(cfg)
        state["cfg"] = cfg
        return jsonify(config=cfgmod.public_view(cfg), status=status())

    @app.post("/api/test_snmp")
    def test_snmp():
        cfg = state["cfg"]
        client = SimClient(cfg["snmp"]) if cfg["simulate"] else SnmpClient(cfg["snmp"])
        try:
            idx, name, speed = client.resolve_interface(cfg["snmp"]["interface"])
            d = client.poll(idx)
            return jsonify(ok=True, message=(
                f"OK: interface {name} (índice {idx}), contadores de {d['counter_bits']} bits, "
                f"CPU {'%.0f%%' % d['cpu'] if d['cpu'] is not None else 'não suportada'}."))
        except SnmpError as exc:
            return jsonify(ok=False, message=str(exc))
        finally:
            client.close()

    @app.get("/api/interfaces")
    def interfaces():
        cfg = state["cfg"]
        client = SimClient(cfg["snmp"]) if cfg["simulate"] else SnmpClient(cfg["snmp"])
        try:
            return jsonify(ok=True, interfaces=[n for _, n in client.list_interfaces()])
        except SnmpError as exc:
            return jsonify(ok=False, message=str(exc))
        finally:
            client.close()

    @app.post("/api/test_email")
    def test_email():
        try:
            send_email(state["cfg"], "[Monitor SNMP] E-mail de teste",
                       "Se você recebeu esta mensagem, o envio de alertas está configurado corretamente.")
            return jsonify(ok=True, message=f"E-mail de teste enviado para {state['cfg']['email']['to']}.")
        except Exception as exc:
            return jsonify(ok=False, message=f"Falha ao enviar e-mail: {exc}")

    app.collector = collector
    return app
