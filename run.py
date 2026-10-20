"""Ponto de entrada:  python run.py [--host 127.0.0.1] [--port 5000]"""
import argparse
import logging

from app.server import create_app

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Monitor de roteador via SNMP")
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 para acessar de outra máquina")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    print(f"Abra http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}")
    # use_reloader=False: o reloader criaria 2 processos (e 2 coletores)
    create_app().run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
