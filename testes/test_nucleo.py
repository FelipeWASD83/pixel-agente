import json
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from nucleo import Tela, normalizar_cor, ErroComando  # noqa: E402


def ler_png(dados):
    """Decodificador mínimo (só filtro 0, 8 bits) para conferir o que o núcleo gera."""
    assert dados[:8] == b"\x89PNG\r\n\x1a\n"
    pos, ihdr, idat = 8, None, b""
    while pos < len(dados):
        n, tipo = struct.unpack(">I4s", dados[pos:pos + 8])
        corpo = dados[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", dados[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(tipo + corpo) & 0xFFFFFFFF
        if tipo == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", corpo)
        elif tipo == b"IDAT":
            idat += corpo
        pos += 12 + n
    w, h, _, tipo_cor, *_ = ihdr
    canais = 4 if tipo_cor == 6 else 3
    bruto = zlib.decompress(idat)
    passo = w * canais + 1
    linhas = []
    for y in range(h):
        assert bruto[y * passo] == 0
        linha = bruto[y * passo + 1:(y + 1) * passo]
        linhas.append([tuple(linha[i:i + canais]) for i in range(0, len(linha), canais)])
    return w, h, linhas


class TestTela(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Tela(8, 6, pasta=self.tmp.name)
        self.eventos = []
        self.t.ao_evento = self.eventos.append

    def tearDown(self):
        self.tmp.cleanup()

    def run_(self, *cmd):
        return self.t.executar(list(cmd))

    def test_cor_normaliza(self):
        self.assertEqual(normalizar_cor("F0a"), "#ff00aa")
        self.assertEqual(normalizar_cor("#ABCDEF"), "#abcdef")
        with self.assertRaises(ErroComando):
            normalizar_cor("vermelho")

    def test_pixel_usa_cor_atual(self):
        self.run_("cor", "#ff0000")
        r = self.run_("pixel", "3", "2")
        self.assertTrue(r["ok"])
        self.assertEqual(self.t.pixels[2 * 8 + 3], "#ff0000")
        self.assertEqual(self.t.pintados(), 1)

    def test_pixel_com_cor_muda_a_atual(self):
        self.run_("pixel", "0", "0", "#00f")
        self.assertEqual(self.t.cor, "#0000ff")

    def test_fora_da_tela_e_erro_e_nao_pinta(self):
        for xy in (("8", "0"), ("0", "6"), ("-1", "0")):
            r = self.run_("pixel", *xy)
            self.assertFalse(r["ok"])
        self.assertEqual(self.t.pintados(), 0)

    def test_argumentos_ruins(self):
        self.assertFalse(self.run_("pixel", "1")["ok"])
        self.assertFalse(self.run_("pixel", "a", "b")["ok"])
        self.assertFalse(self.run_("voar")["ok"])
        self.assertFalse(self.run_("cor", "azul")["ok"])
        self.assertFalse(self.run_("novo", "0", "10")["ok"])
        self.assertFalse(self.run_("novo", "257", "10")["ok"])

    def test_apagar_e_desfazer(self):
        self.run_("pixel", "1", "1")
        self.run_("cor", "#00ff00")
        self.run_("pixel", "1", "1")
        self.run_("apagar", "1", "1")
        self.assertEqual(self.t.pintados(), 0)
        self.run_("desfazer")
        self.assertEqual(self.t.pixels[1 * 8 + 1], "#00ff00")
        self.run_("desfazer", "2")
        self.assertEqual(self.t.pintados(), 0)
        self.assertEqual(self.run_("desfazer")["msg"], "nada para desfazer")

    def test_pintar_igual_nao_enche_o_desfazer(self):
        self.run_("pixel", "1", "1")
        self.run_("pixel", "1", "1")
        self.assertEqual(len(self.t.undo), 1)

    def test_limpar_desfaz_em_bloco(self):
        for x in range(3):
            self.run_("pixel", str(x), "0")
        self.run_("limpar")
        self.assertEqual(self.t.pintados(), 0)
        self.run_("desfazer")
        self.assertEqual(self.t.pintados(), 3)

    def test_novo_muda_resolucao_e_zera_desfazer(self):
        self.run_("pixel", "0", "0")
        self.run_("novo", "16", "4")
        self.assertEqual((self.t.largura, self.t.altura, self.t.pintados(), len(self.t.undo)), (16, 4, 0, 0))

    def test_eventos_em_sequencia(self):
        self.run_("pixel", "0", "0")
        self.run_("pixel", "9", "9")  # erro também vira evento (log)
        self.run_("novo", "4", "4")
        seqs = [e["seq"] for e in self.eventos]
        self.assertEqual(seqs, list(range(1, len(seqs) + 1)))
        self.assertEqual([e["tipo"] for e in self.eventos], ["pixels", "log", "estado"])
        self.assertEqual(self.eventos[0]["mudancas"], [[0, 0, "#000000"]])

    def test_pegar(self):
        self.run_("pixel", "2", "2", "#123456")
        self.assertIn("#123456", self.run_("pegar", "2", "2")["msg"])
        self.assertIn("vazio", self.run_("pegar", "0", "0")["msg"])

    def test_mapa(self):
        self.run_("pixel", "0", "0", "#ff0000")
        self.run_("pixel", "1", "0", "#0000ff")
        self.run_("pixel", "2", "0", "#ff0000")
        msg = self.run_("mapa")["msg"]
        self.assertIn("a = #ff0000 (2)", msg)
        self.assertIn("b = #0000ff (1)", msg)
        self.assertIn("  0 aba.....", msg)
        recorte = self.run_("mapa", "1", "0", "2", "1")["msg"]
        self.assertIn("  0 ab", recorte)  # as letras seguem a ordem de aparição no recorte
        self.assertFalse(self.run_("mapa", "1", "2")["ok"])

    def test_salvar_e_abrir(self):
        self.run_("pixel", "5", "4", "#abcdef")
        self.assertTrue(self.run_("salvar", "teste", "3")["ok"])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, "teste.json")))
        self.run_("novo", "2", "2")
        self.assertTrue(self.run_("abrir", "teste")["ok"])
        self.assertEqual((self.t.largura, self.t.altura), (8, 6))
        self.assertEqual(self.t.pixels[4 * 8 + 5], "#abcdef")
        self.assertIn("teste", self.run_("listar")["msg"])
        w, h, linhas = ler_png(open(os.path.join(self.tmp.name, "teste.png"), "rb").read())
        self.assertEqual((w, h), (24, 18))
        self.assertEqual(linhas[4 * 3][5 * 3], (0xab, 0xcd, 0xef, 255))
        self.assertEqual(linhas[0][0][3], 0)

    def test_nome_de_arquivo_nao_escapa_da_pasta(self):
        for nome in ("../fora", "a/b", "..", "_atual", "x y"):
            self.assertFalse(self.run_("salvar", nome)["ok"], nome)
        self.assertFalse(self.run_("abrir", "../fora")["ok"])

    def test_abrir_arquivo_estragado(self):
        with open(os.path.join(self.tmp.name, "ruim.json"), "w") as f:
            json.dump({"largura": 2, "altura": 2, "pixels": [["#fff"]]}, f)
        self.assertFalse(self.run_("abrir", "ruim")["ok"])
        self.assertEqual((self.t.largura, self.t.altura), (8, 6))

    def test_png_do_ver(self):
        self.run_("pixel", "1", "0", "#ff0000")
        png, w, h = self.t.renderizar(escala=2)
        lw, lh, linhas = ler_png(png)
        self.assertEqual((lw, lh, w, h), (16, 12, 16, 12))
        self.assertEqual(linhas[0][2], (255, 0, 0))
        self.assertEqual(linhas[1][3], (255, 0, 0))
        png, w, h = self.t.renderizar(escala=4, grade=True, recorte=(1, 0, 2, 2))
        self.assertEqual((w, h), (8, 8))
        self.assertEqual(ler_png(png)[2][2][2], (255, 0, 0))  # dentro da célula, fora da linha da grade
        with self.assertRaises(ErroComando):
            self.t.renderizar(recorte=(7, 0, 5, 1))
        with self.assertRaises(ErroComando):
            self.t.renderizar(escala=65)


if __name__ == "__main__":
    unittest.main()
