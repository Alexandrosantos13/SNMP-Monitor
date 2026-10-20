"""Testes unitários (sem roteador):  python -m unittest discover -s tests -v"""
import copy, os, sys, tempfile, time, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config
from app.alerts import AlertEngine, send_email
from app.collector import rate_bps
from app.storage import Storage


class RateTests(unittest.TestCase):
    def test_basico(self): self.assertEqual(rate_bps(0, 1_250_000, 10), 1_000_000)
    def test_counter64_reset(self): self.assertIsNone(rate_bps(1000, 500, 5, 64))
    def test_counter32_wrap(self): self.assertEqual(round(rate_bps(2**32 - 1000, 1000, 2, 32)), 8000)
    def test_taxa_absurda(self): self.assertIsNone(rate_bps(0, 10**12, 1, 64, 1e9))
    def test_dt_invalido(self):
        self.assertIsNone(rate_bps(5, 6, 0)); self.assertIsNone(rate_bps(5, 6, -1))


class ConfigTests(unittest.TestCase):
    def setUp(self): self.cur = copy.deepcopy(config.DEFAULTS)
    def errs(self, **patch):
        new = copy.deepcopy(patch); return config.validate(new, self.cur)[1]
    def test_intervalo(self):
        self.assertTrue(self.errs(collect_interval=0)); self.assertTrue(self.errs(collect_interval=-5))
        self.assertFalse(self.errs(collect_interval=2))
    def test_email(self):
        self.assertTrue(self.errs(email={"to": "abc"})); self.assertFalse(self.errs(email={"to": "a@b.com"}))
    def test_email_obrigatorio_se_ativo(self): self.assertTrue(self.errs(alerts={"email": True}))
    def test_cpu_max(self): self.assertTrue(self.errs(alerts={"threshold_cpu": 150}))
    def test_segredo_mantido(self):
        self.cur["snmp"]["community"] = "segredo"
        cfg, e = config.validate({"snmp": {"community": "********"}}, self.cur)
        self.assertEqual(cfg["snmp"]["community"], "segredo")


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.st = Storage(os.path.join(tempfile.mkdtemp(), "t.db"))
        self.cfg = copy.deepcopy(config.DEFAULTS)
        self.cfg["alerts"].update(threshold_cpu=80, cooldown_s=60, popup=True, email=False)
        self.eng = AlertEngine(self.st, lambda: self.cfg)
    def test_transicao_e_cooldown(self):
        e = self.eng
        e.evaluate(0, None, None, 50);  self.assertEqual(len(e.feed_since(0)), 0)   # normal
        e.evaluate(1, None, None, 90);  self.assertEqual(len(e.feed_since(0)), 1)   # transição -> alerta
        e.evaluate(2, None, None, 95);  self.assertEqual(len(e.feed_since(0)), 1)   # continua excedido: silêncio
        e.evaluate(70, None, None, 95); self.assertEqual(len(e.feed_since(0)), 2)   # passou o cooldown
        e.evaluate(71, None, None, 10)                                              # recuperou
        e.evaluate(72, None, None, 99); self.assertEqual(len(e.feed_since(0)), 3)   # rearmado
    def test_conteudo_popup(self):
        self.eng.evaluate(5, None, None, 90.0)
        a = self.eng.feed_since(0)[0]
        for k in ("label", "value", "limit", "when"): self.assertTrue(a[k])
    def test_in_out_separados(self):
        self.cfg["alerts"].update(threshold_in_bps=1e6, threshold_out_bps=0)
        self.eng.evaluate(1, 5e6, 5e6, 0)
        self.assertEqual([a["metric"] for a in self.eng.feed_since(0)], ["in"])


class StorageTests(unittest.TestCase):
    def test_filtro_periodo_e_vazio(self):
        st = Storage(os.path.join(tempfile.mkdtemp(), "t.db"))
        for t in range(100, 110): st.add_sample(t, t * 1.0, t * 2.0, 10)
        self.assertEqual(st.history(103, 105)["total"], 3)
        self.assertEqual(st.history(500, 600)["total"], 0)


class EmailTests(unittest.TestCase):
    def test_envio_smtp_local(self):
        try:
            from aiosmtpd.controller import Controller
        except ImportError:
            self.skipTest("aiosmtpd não instalado")
        got = []
        class H:
            async def handle_DATA(self, server, session, envelope):
                got.append(envelope.content.decode()); return "250 OK"
        ctl = Controller(H(), hostname="127.0.0.1", port=8026); ctl.start()
        try:
            cfg = copy.deepcopy(config.DEFAULTS)
            cfg["email"].update(to="dest@exemplo.com", smtp_host="127.0.0.1", smtp_port=8026, security="none", **{"from": "mon@exemplo.com"})
            send_email(cfg, "Assunto", "corpo da mensagem")
        finally:
            ctl.stop()
        self.assertEqual(len(got), 1); self.assertIn("corpo da mensagem", got[0])
    def test_falha_smtp_gera_excecao(self):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg["email"].update(to="a@b.com", smtp_host="127.0.0.1", smtp_port=1, security="none")
        with self.assertRaises(Exception): send_email(cfg, "x", "y")


if __name__ == "__main__":
    unittest.main()
