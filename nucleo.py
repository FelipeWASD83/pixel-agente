"""Núcleo do pixel-agente: a tela, os comandos e a geração de PNG.

Não sabe nada de rede. O servidor chama `Tela.executar(["pixel", "3", "4"])` e
repassa os eventos que a tela emite para a janela.
"""
import json
import os
import re
import struct
import time
import zlib
from collections import deque

LIMITE = 256
LIMITE_PNG = 4096
MAX_DESFAZER = 5000

RE_NOME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
RE_COR = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
LETRAS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# nome: (mínimo de argumentos, máximo, uso, o que faz)
COMANDOS = {
    "novo": (2, 2, "novo LARGURA ALTURA", "escolhe a resolução (1 a 256) e limpa a tela"),
    "cor": (1, 1, "cor #rrggbb", "escolhe a cor atual (#rgb também vale)"),
    "pixel": (2, 3, "pixel X Y [#cor]", "pinta 1 pixel com a cor atual (ou com a cor dada, que vira a atual)"),
    "apagar": (2, 2, "apagar X Y", "deixa 1 pixel transparente"),
    "desfazer": (0, 1, "desfazer [N]", "desfaz as últimas N ações (padrão 1)"),
    "limpar": (0, 0, "limpar", "apaga a tela inteira (dá para desfazer)"),
    "pegar": (2, 2, "pegar X Y", "diz a cor de um pixel"),
    "estado": (0, 0, "estado", "resolução, cor atual e quantos pixels estão pintados"),
    "mapa": (0, 4, "mapa [X Y LARGURA ALTURA]", "desenho em texto (uma letra por cor), para conferir sem ver imagem"),
    "salvar": (1, 2, "salvar NOME [ESCALA]", "grava desenhos/NOME.json e NOME.png (ESCALA amplia o png, padrão 1)"),
    "abrir": (1, 1, "abrir NOME", "carrega desenhos/NOME.json"),
    "listar": (0, 0, "listar", "lista os desenhos salvos"),
}


class ErroComando(Exception):
    pass


def normalizar_cor(texto):
    m = RE_COR.match(str(texto).strip())
    if not m:
        raise ErroComando(f"cor inválida: {texto!r} (use #rgb ou #rrggbb)")
    h = m.group(1).lower()
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h


def _inteiro(texto, nome):
    try:
        return int(str(texto).strip())
    except ValueError:
        raise ErroComando(f"{nome} precisa ser número inteiro, veio {texto!r}") from None


def _rgb(cor):
    return bytes(int(cor[i:i + 2], 16) for i in (1, 3, 5))


def _chunk(tipo, dados):
    return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)


def codificar_png(largura, altura, linhas, canais):
    """linhas: lista de bytes, cada uma com largura*canais bytes (RGB ou RGBA)."""
    bruto = b"".join(b"\x00" + linha for linha in linhas)
    ihdr = struct.pack(">IIBBBBB", largura, altura, 8, 6 if canais == 4 else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bruto, 6)) + _chunk(b"IEND", b"")


def escrever_atomico(caminho, dados):
    tmp = caminho + ".tmp"
    with open(tmp, "wb") as f:
        f.write(dados)
    os.replace(tmp, caminho)


class Tela:
    def __init__(self, largura=32, altura=32, pasta=None):
        self.pasta = pasta
        self.seq = 0
        self.log = deque(maxlen=200)
        self.ao_evento = None
        self.cor = "#000000"
        self._zerar(largura, altura)

    # ---------- estado ----------

    def _zerar(self, largura, altura):
        self.largura = largura
        self.altura = altura
        self.pixels = [""] * (largura * altura)
        self.undo = deque()

    def pintados(self):
        return sum(1 for p in self.pixels if p)

    def para_dict(self):
        w = self.largura
        return {
            "versao": 1,
            "largura": w,
            "altura": self.altura,
            "cor": self.cor,
            "pixels": [self.pixels[y * w:(y + 1) * w] for y in range(self.altura)],
        }

    def carregar_dict(self, d):
        try:
            w, h = int(d["largura"]), int(d["altura"])
            linhas = d["pixels"]
            cor = normalizar_cor(d.get("cor", "#000000"))
        except (KeyError, TypeError, ValueError) as e:
            raise ErroComando(f"arquivo de desenho inválido ({e})") from None
        if not (1 <= w <= LIMITE and 1 <= h <= LIMITE):
            raise ErroComando(f"resolução fora de 1 a {LIMITE}: {w}x{h}")
        if not isinstance(linhas, list) or len(linhas) != h or any(not isinstance(l, list) or len(l) != w for l in linhas):
            raise ErroComando("arquivo de desenho inválido (as linhas não batem com a resolução)")
        pixels = []
        for linha in linhas:
            for c in linha:
                pixels.append(normalizar_cor(c) if c else "")
        self._zerar(w, h)
        self.pixels = pixels
        self.cor = cor

    def para_estado(self):
        return {
            "largura": self.largura,
            "altura": self.altura,
            "cor": self.cor,
            "pixels": self.pixels,
            "pintados": self.pintados(),
            "desfazer": len(self.undo),
            "seq": self.seq,
            "log": list(self.log),
        }

    def _emitir(self, tipo, msg, nivel="info", **dados):
        self.seq += 1
        t = time.strftime("%H:%M:%S")
        self.log.append({"t": t, "msg": msg, "nivel": nivel})
        if self.ao_evento:
            self.ao_evento({"seq": self.seq, "tipo": tipo, "msg": msg, "nivel": nivel, "t": t, **dados})

    def registrar(self, msg, nivel="leve"):
        self._emitir("log", msg, nivel)

    # ---------- execução ----------

    def executar(self, cmd):
        """cmd: lista de strings, igual ao que sobra da linha de comando. Devolve {"ok", "msg"}."""
        try:
            if not cmd:
                raise ErroComando("comando vazio")
            nome, args = str(cmd[0]).lower(), [str(a) for a in cmd[1:]]
            if nome not in COMANDOS:
                raise ErroComando(f"comando desconhecido: {nome} (veja: python px.py ajuda)")
            minimo, maximo, uso, _ = COMANDOS[nome]
            if not (minimo <= len(args) <= maximo):
                raise ErroComando(f"uso: {uso}")
            msg = getattr(self, "cmd_" + nome)(*args)
            return {"ok": True, "msg": msg}
        except ErroComando as e:
            self._emitir("log", str(e), "erro")
            return {"ok": False, "msg": str(e)}

    def _celula(self, x, y):
        x, y = _inteiro(x, "x"), _inteiro(y, "y")
        if not (0 <= x < self.largura and 0 <= y < self.altura):
            raise ErroComando(f"({x},{y}) fora da tela, que vai de (0,0) a ({self.largura - 1},{self.altura - 1})")
        return x, y

    def _empilhar(self, grupo):
        self.undo.append(grupo)
        while len(self.undo) > MAX_DESFAZER:
            self.undo.popleft()

    def _trocar(self, x, y, nova):
        i = y * self.largura + x
        velha = self.pixels[i]
        if velha == nova:
            return False
        self._empilhar([(i, velha)])
        self.pixels[i] = nova
        return True

    # ---------- comandos ----------

    def cmd_novo(self, l, a):
        l, a = _inteiro(l, "largura"), _inteiro(a, "altura")
        if not (1 <= l <= LIMITE and 1 <= a <= LIMITE):
            raise ErroComando(f"resolução vai de 1 a {LIMITE} por lado, veio {l}x{a}")
        self._zerar(l, a)
        msg = f"tela nova {l}x{a}"
        self._emitir("estado", msg)
        return msg

    def cmd_cor(self, c):
        self.cor = normalizar_cor(c)
        msg = f"cor {self.cor}"
        self._emitir("cor", msg, cor=self.cor)
        return msg

    def cmd_pixel(self, x, y, c=None):
        x, y = self._celula(x, y)
        if c is not None:
            self.cor = normalizar_cor(c)
        mudou = self._trocar(x, y, self.cor)
        msg = f"pixel ({x},{y}) {self.cor}" + ("" if mudou else " (já estava assim)")
        if mudou:
            self._emitir("pixels", msg, mudancas=[[x, y, self.cor]], ultimo=[x, y], cor=self.cor)
        else:
            self._emitir("log", msg, cor=self.cor)
        return msg

    def cmd_apagar(self, x, y):
        x, y = self._celula(x, y)
        mudou = self._trocar(x, y, "")
        msg = f"apagou ({x},{y})" + ("" if mudou else " (já estava vazio)")
        if mudou:
            self._emitir("pixels", msg, mudancas=[[x, y, ""]], ultimo=[x, y], cor=self.cor)
        else:
            self._emitir("log", msg)
        return msg

    def cmd_desfazer(self, n="1"):
        n = _inteiro(n, "N")
        if n < 1:
            raise ErroComando("N precisa ser 1 ou mais")
        mudancas, feito = [], 0
        while feito < n and self.undo:
            for i, velha in reversed(self.undo.pop()):
                self.pixels[i] = velha
                mudancas.append([i % self.largura, i // self.largura, velha])
            feito += 1
        if not feito:
            msg = "nada para desfazer"
            self._emitir("log", msg)
            return msg
        msg = f"desfez {feito} ação(ões)"
        self._emitir("pixels", msg, mudancas=mudancas, ultimo=mudancas[-1][:2], cor=self.cor)
        return msg

    def cmd_limpar(self):
        grupo = [(i, p) for i, p in enumerate(self.pixels) if p]
        if not grupo:
            msg = "a tela já está vazia"
            self._emitir("log", msg)
            return msg
        self._empilhar(grupo)
        self.pixels = [""] * len(self.pixels)
        msg = f"tela limpa ({len(grupo)} pixels)"
        self._emitir("estado", msg)
        return msg

    def cmd_pegar(self, x, y):
        x, y = self._celula(x, y)
        c = self.pixels[y * self.largura + x]
        msg = f"({x},{y}) = {c}" if c else f"({x},{y}) vazio"
        self._emitir("log", msg, "leve")
        return msg

    def cmd_estado(self):
        msg = (f"tela {self.largura}x{self.altura} | cor {self.cor} | "
               f"pintados {self.pintados()} | desfazer {len(self.undo)}")
        self._emitir("log", "estado", "leve")
        return msg

    def _recorte(self, args):
        if not args:
            return 0, 0, self.largura, self.altura
        x0, y0, l, a = (_inteiro(v, nome) for v, nome in zip(args, ("x", "y", "largura", "altura")))
        if l < 1 or a < 1 or x0 < 0 or y0 < 0 or x0 + l > self.largura or y0 + a > self.altura:
            raise ErroComando(f"recorte fora da tela (a tela é {self.largura}x{self.altura})")
        return x0, y0, l, a

    def cmd_mapa(self, *args):
        if len(args) not in (0, 4):
            raise ErroComando("uso: mapa [X Y LARGURA ALTURA]")
        x0, y0, l, a = self._recorte(args)
        letras, contagem, linhas = {}, {}, []
        for y in range(y0, y0 + a):
            linha = []
            for x in range(x0, x0 + l):
                c = self.pixels[y * self.largura + x]
                if not c:
                    linha.append(".")
                    continue
                if c not in letras:
                    letras[c] = LETRAS[len(letras)] if len(letras) < len(LETRAS) else "?"
                contagem[c] = contagem.get(c, 0) + 1
                linha.append(letras[c])
            linhas.append(f"{y:3d} " + "".join(linha))
        regua = ["    " + "".join(str((x // 10) % 10) if x % 10 == 0 else " " for x in range(x0, x0 + l)),
                 "    " + "".join(str(x % 10) for x in range(x0, x0 + l))]
        legenda = [". = vazio"] + [f"{letras[c]} = {c} ({contagem[c]})" for c in letras]
        self._emitir("log", f"mapa {l}x{a}", "leve")
        return "\n".join(legenda + regua + linhas)

    def _caminho(self, nome, ext):
        if not self.pasta:
            raise ErroComando("este servidor não tem pasta de desenhos")
        if not RE_NOME.match(nome):
            raise ErroComando("nome inválido: use letras, números, - e _ (até 64, sem começar por símbolo)")
        return os.path.join(self.pasta, nome + ext)

    def cmd_salvar(self, nome, escala="1"):
        esc = _inteiro(escala, "escala")
        caminho = self._caminho(nome, ".json")
        png, w, h = self.renderizar(escala=esc, alfa=True)
        os.makedirs(self.pasta, exist_ok=True)
        escrever_atomico(caminho, json.dumps(self.para_dict(), separators=(",", ":")).encode("utf-8"))
        escrever_atomico(self._caminho(nome, ".png"), png)
        msg = f"salvo {nome}.json e {nome}.png ({w}x{h}) em {self.pasta}"
        self._emitir("log", msg)
        return msg

    def cmd_abrir(self, nome):
        caminho = self._caminho(nome, ".json")
        if not os.path.isfile(caminho):
            raise ErroComando(f"não existe o desenho {nome!r} (veja: listar)")
        try:
            with open(caminho, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError) as e:
            raise ErroComando(f"não consegui ler {nome}.json ({e})") from None
        self.carregar_dict(d)
        msg = f"abriu {nome} ({self.largura}x{self.altura})"
        self._emitir("estado", msg)
        return msg

    def cmd_listar(self):
        nomes = []
        if self.pasta and os.path.isdir(self.pasta):
            nomes = sorted(f[:-5] for f in os.listdir(self.pasta) if f.endswith(".json") and RE_NOME.match(f[:-5]))
        self._emitir("log", "listar", "leve")
        return "desenhos: " + (", ".join(nomes) if nomes else "(nenhum)")

    # ---------- imagem ----------

    def renderizar(self, escala=None, grade=False, recorte=None, alfa=False):
        """Devolve (png, largura_px, altura_px). Transparente vira xadrez, salvo com alfa=True."""
        if recorte:
            x0, y0, l, a = recorte
            if l < 1 or a < 1 or x0 < 0 or y0 < 0 or x0 + l > self.largura or y0 + a > self.altura:
                raise ErroComando(f"recorte fora da tela (a tela é {self.largura}x{self.altura})")
        else:
            x0, y0, l, a = 0, 0, self.largura, self.altura
        if escala is None:
            escala = max(1, min(32, 512 // max(l, a)))
        if not 1 <= escala <= 64:
            raise ErroComando("escala vai de 1 a 64")
        if l * escala > LIMITE_PNG or a * escala > LIMITE_PNG:
            raise ErroComando(f"imagem passaria de {LIMITE_PNG}px por lado; use escala menor")
        canais = 4 if alfa else 3
        com_grade = grade and not alfa and escala >= 4
        claro, escuro = bytes((247, 247, 247)), bytes((222, 222, 222))
        cache = {}

        def pedaco(x, y):
            c = self.pixels[y * self.largura + x]
            if c:
                if c not in cache:
                    cache[c] = _rgb(c) + (b"\xff" if alfa else b"")
                return cache[c]
            if alfa:
                return b"\x00\x00\x00\x00"
            return claro if (x + y) % 2 == 0 else escuro

        def cor_linha(n):
            return bytes((70, 70, 70)) if n % 8 == 0 else bytes((165, 165, 165))

        linhas = []
        for j in range(a):
            y = y0 + j
            linha = bytearray()
            for i in range(l):
                linha += pedaco(x0 + i, y) * escala
            if com_grade:
                for i in range(l):
                    ini = i * escala * canais
                    linha[ini:ini + canais] = cor_linha(x0 + i)
                topo = bytearray()
                for i in range(l):
                    topo += cor_linha(y) * escala
                linhas.append(bytes(topo))
                linhas.extend([bytes(linha)] * (escala - 1))
            else:
                linhas.extend([bytes(linha)] * escala)
        return codificar_png(l * escala, a * escala, linhas, canais), l * escala, a * escala
