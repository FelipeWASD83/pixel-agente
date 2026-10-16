import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

AQUI = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, AQUI)
import servidor  # noqa: E402


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.srv = servidor.criar_servidor(0, cls.tmp.name)
        cls.porta = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def pedir(self, metodo, caminho, corpo=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        h = dict(headers or {})
        dados = None
        if corpo is not None:
            dados = json.dumps(corpo)
            h.setdefault("Content-Type", "application/json")
        c.request(metodo, caminho, dados, h)
        r = c.getresponse()
        return r.status, r.read(), r

    def cmds(self, *cmds, **extra):
        status, corpo, _ = self.pedir("POST", "/api/cmd", {"cmds": [list(c) for c in cmds], **extra})
        return status, json.loads(corpo)


class TestServidor(Base):
    def test_pagina_e_estado(self):
        status, corpo, _ = self.pedir("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Pixel Agente", corpo)
        self.assertEqual(self.pedir("GET", "/app.js")[0], 200)
        status, corpo, _ = self.pedir("GET", "/api/estado")
        e = json.loads(corpo)
        self.assertEqual(status, 200)
        self.assertEqual(len(e["pixels"]), e["largura"] * e["altura"])

    def test_comando_muda_estado(self):
        self.cmds(["novo", "4", "4"])
        status, r = self.cmds(["cor", "#102030"], ["pixel", "1", "2"])
        self.assertTrue(all(x["ok"] for x in r["resultados"]))
        e = json.loads(self.pedir("GET", "/api/estado")[1])
        self.assertEqual(e["pixels"][2 * 4 + 1], "#102030")
        self.assertEqual(e["pintados"], 1)

    def test_lote_para_no_primeiro_erro(self):
        self.cmds(["novo", "4", "4"])
        _, r = self.cmds(["pixel", "0", "0"], ["pixel", "9", "9"], ["pixel", "1", "1"])
        self.assertEqual(r["parou_em"], 1)
        self.assertEqual(len(r["resultados"]), 2)
        _, r = self.cmds(["pixel", "0", "0"], ["pixel", "9", "9"], ["pixel", "1", "1"], continuar=True)
        self.assertIsNone(r["parou_em"])
        self.assertEqual(len(r["resultados"]), 3)

    def test_png(self):
        status, corpo, r = self.pedir("GET", "/api/png?escala=2&grade=1")
        self.assertEqual(status, 200)
        self.assertEqual(corpo[:8], b"\x89PNG\r\n\x1a\n")
        self.assertIn("X-Largura", dict(r.getheaders()))
        self.assertEqual(self.pedir("GET", "/api/png?escala=999")[0], 400)
        self.assertEqual(self.pedir("GET", "/api/png?recorte=1,2")[0], 400)

    def test_nao_serve_fora_da_web(self):
        self.assertEqual(self.pedir("GET", "/../servidor.py")[0], 404)
        self.assertEqual(self.pedir("GET", "/..%2fservidor.py")[0], 404)

    def test_protecoes(self):
        # Host de outro site (DNS rebinding)
        self.assertEqual(self.pedir("GET", "/api/estado", headers={"Host": "exemplo.com"})[0], 403)
        # POST de página de outro site
        self.assertEqual(self.pedir("POST", "/api/cmd", {"cmds": [["limpar"]]}, {"Origin": "http://exemplo.com"})[0], 403)
        # formulário simples (sem JSON) não passa
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=5)
        c.request("POST", "/api/cmd", "x=1", {"Content-Type": "text/plain"})
        self.assertEqual(c.getresponse().status, 415)
        self.assertEqual(self.cmds()[0], 400)

    def test_eventos_ao_vivo(self):
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=10)
        c.request("GET", "/api/eventos")
        r = c.getresponse()
        self.assertEqual(r.status, 200)
        self.assertEqual(r.fp.readline(), b": ok\n")
        r.fp.readline()
        self.cmds(["novo", "5", "5"], ["pixel", "2", "3", "#ff00ff"])
        vistos = []
        while len(vistos) < 2:
            linha = r.fp.readline()
            if linha.startswith(b"data: "):
                vistos.append(json.loads(linha[6:]))
        self.assertEqual(vistos[0]["tipo"], "estado")
        self.assertEqual(vistos[1]["mudancas"], [[2, 3, "#ff00ff"]])
        self.assertEqual(vistos[1]["seq"], vistos[0]["seq"] + 1)
        c.close()


class TestAutosave(unittest.TestCase):
    def test_volta_do_jeito_que_parou(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = servidor.criar_servidor(0, tmp)
            s.tela.executar(["novo", "6", "3"])
            s.tela.executar(["pixel", "5", "2", "#abcdef"])
            s.salvar_atual()
            s.server_close()
            s2 = servidor.criar_servidor(0, tmp)
            self.assertEqual((s2.tela.largura, s2.tela.altura), (6, 3))
            self.assertEqual(s2.tela.pixels[2 * 6 + 5], "#abcdef")
            s2.server_close()


class TestPx(Base):
    def px(self, *args, entrada=None):
        env = dict(os.environ, PIXEL_PORTA=str(self.porta), PIXEL_SEM_AUTOSTART="1")
        return subprocess.run([sys.executable, os.path.join(AQUI, "px.py"), *args], input=entrada,
                              capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)

    def test_comandos_e_codigo_de_saida(self):
        self.assertEqual(self.px("novo", "10", "10").returncode, 0)
        r = self.px("cor", "#ff0000")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok cor #ff0000"))
        r = self.px("pixel", "3", "4")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok pixel (3,4) #ff0000"))
        r = self.px("pixel", "30", "4")
        self.assertEqual(r.returncode, 1)
        self.assertIn("fora da tela", r.stdout)

    def test_lote_por_stdin_e_inline(self):
        self.px("novo", "10", "10")
        r = self.px("lote", "-", "--ritmo", "0", entrada="// comentário\ncor #00ff00\npixel 1 1\n\npixel 2 1\n")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "ok lote: 3 comandos executados"))
        r = self.px("lote", "-c", "pixel 5 5; pixel 6 5", "--ritmo", "0")
        self.assertEqual(r.returncode, 0)
        e = json.loads(self.pedir("GET", "/api/estado")[1])
        self.assertEqual(e["pintados"], 4)
        r = self.px("lote", "-c", "pixel 5 5; pixel 99 5; pixel 7 7", "--ritmo", "0")
        self.assertEqual(r.returncode, 1)
        self.assertIn("comando 2", r.stdout)

    def test_ver_salva_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = os.path.join(tmp, "a.png")
            r = self.px("ver", "--escala", "4", "--grade", "--saida", destino)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(destino, "rb") as f:
                self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

    def test_ajuda_e_sem_servidor(self):
        self.assertIn("pixel X Y", self.px("ajuda").stdout)
        env = dict(os.environ, PIXEL_PORTA="1", PIXEL_SEM_AUTOSTART="1")
        r = subprocess.run([sys.executable, os.path.join(AQUI, "px.py"), "estado"], capture_output=True, text=True,
                           encoding="utf-8", env=env, timeout=30)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
