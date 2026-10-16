"""Servidor do pixel-agente: guarda a tela, recebe os comandos e avisa a janela em tempo real.

Só em 127.0.0.1. Uso: python servidor.py [--porta 8750] [--dados PASTA] [--abrir]
"""
import argparse
import json
import mimetypes
import os
import queue
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from nucleo import ErroComando, Tela

VERSAO = "1.0.0"
AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_WEB = os.path.join(AQUI, "web")
PORTA_PADRAO = 8750
MAX_COMANDOS = 20000
MAX_RITMO_MS = 2000
ARQUIVO_ATUAL = "_atual.json"


class Servidor(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # no Windows, True deixa dois servidores abrirem a mesma porta

    def __init__(self, endereco, pasta_dados):
        super().__init__(endereco, Handler)
        self.lock = threading.RLock()
        self.assinantes = []
        self.sujo = False
        self.pasta_dados = pasta_dados
        self.tela = Tela(32, 32, pasta=pasta_dados)
        self.tela.ao_evento = self._transmitir
        self._carregar_atual()

    # ---------- eventos e autosave ----------

    def _transmitir(self, ev):
        if ev["tipo"] != "log":
            self.sujo = True
        for q in list(self.assinantes):
            try:
                q.put_nowait(ev)
            except queue.Full:
                # cliente travado: derruba o fluxo, a janela reconecta e recarrega tudo
                self.assinantes.remove(q)
                while not q.empty():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
                q.put_nowait(None)

    def _carregar_atual(self):
        caminho = os.path.join(self.pasta_dados, ARQUIVO_ATUAL)
        try:
            with open(caminho, encoding="utf-8") as f:
                self.tela.carregar_dict(json.load(f))
        except (OSError, ValueError, ErroComando):
            pass

    def salvar_atual(self):
        with self.lock:
            dados = json.dumps(self.tela.para_dict(), separators=(",", ":")).encode("utf-8")
            self.sujo = False
        try:
            os.makedirs(self.pasta_dados, exist_ok=True)
            from nucleo import escrever_atomico
            escrever_atomico(os.path.join(self.pasta_dados, ARQUIVO_ATUAL), dados)
        except OSError as e:
            print(f"aviso: não consegui gravar o autosave ({e})", file=sys.stderr)

    def laco_autosave(self):
        while True:
            time.sleep(1)
            if self.sujo:
                self.salvar_atual()


class Handler(BaseHTTPRequestHandler):
    server_version = "pixel-agente"
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    # ---------- utilidades ----------

    def _permitidos(self):
        p = self.server.server_address[1]
        return {f"127.0.0.1:{p}", f"localhost:{p}"}

    def _host_ok(self):
        return self.headers.get("Host", "") in self._permitidos()

    def _origem_ok(self):
        origem = self.headers.get("Origin")
        return origem is None or origem in {f"http://{h}" for h in self._permitidos()}

    def _responder(self, codigo, corpo, tipo, extra=None):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, codigo, obj, extra=None):
        self._responder(codigo, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", extra)

    # ---------- GET ----------

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"ok": False, "msg": "host não permitido"})
        url = urlparse(self.path)
        rota = url.path
        if rota == "/api/estado":
            with self.server.lock:
                estado = self.server.tela.para_estado()
            estado["versao"] = VERSAO
            return self._json(200, estado)
        if rota == "/api/eventos":
            return self._eventos()
        if rota == "/api/png":
            return self._png(parse_qs(url.query))
        return self._estatico(rota)

    def _estatico(self, rota):
        if rota == "/":
            rota = "/index.html"
        alvo = os.path.normpath(os.path.join(PASTA_WEB, rota.lstrip("/")))
        if not alvo.startswith(PASTA_WEB + os.sep) or not os.path.isfile(alvo):
            return self._json(404, {"ok": False, "msg": "não encontrado"})
        tipo = mimetypes.guess_type(alvo)[0] or "application/octet-stream"
        if tipo.startswith("text/") or tipo.endswith("javascript"):
            tipo += "; charset=utf-8"
        with open(alvo, "rb") as f:
            self._responder(200, f.read(), tipo)

    def _png(self, q):
        try:
            escala = int(q["escala"][0]) if "escala" in q else None
            grade = q.get("grade", ["0"])[0] == "1"
            recorte = None
            if "recorte" in q:
                partes = q["recorte"][0].split(",")
                if len(partes) != 4:
                    raise ErroComando("recorte é X,Y,LARGURA,ALTURA")
                recorte = tuple(int(p) for p in partes)
        except (ValueError, ErroComando) as e:
            return self._json(400, {"ok": False, "msg": f"parâmetro inválido: {e}"})
        try:
            with self.server.lock:
                png, w, h = self.server.tela.renderizar(escala=escala, grade=grade, recorte=recorte)
                self.server.tela.registrar(f"ver {w}x{h}px" + (" com grade" if grade else "")
                                           + (f" recorte {q['recorte'][0]}" if recorte else ""))
        except ErroComando as e:
            return self._json(400, {"ok": False, "msg": str(e)})
        self._responder(200, png, "image/png", {"X-Largura": str(w), "X-Altura": str(h)})

    def _eventos(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = queue.Queue(maxsize=2000)
        with self.server.lock:
            self.server.assinantes.append(q)
        try:
            self.wfile.write(b": ok\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    pedaco = b": ping\n\n"
                else:
                    if ev is None:
                        break
                    pedaco = b"data: " + json.dumps(ev, ensure_ascii=False).encode("utf-8") + b"\n\n"
                self.wfile.write(pedaco)
                self.wfile.flush()
        except OSError:
            pass
        finally:
            with self.server.lock:
                if q in self.server.assinantes:
                    self.server.assinantes.remove(q)

    # ---------- POST ----------

    def do_POST(self):
        if not self._host_ok() or not self._origem_ok():
            return self._json(403, {"ok": False, "msg": "origem não permitida"})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._json(415, {"ok": False, "msg": "use Content-Type: application/json"})
        try:
            tamanho = int(self.headers.get("Content-Length", "0"))
            corpo = json.loads(self.rfile.read(tamanho) or b"{}") if 0 <= tamanho <= 8_000_000 else None
        except ValueError:
            corpo = None
        if not isinstance(corpo, dict):
            return self._json(400, {"ok": False, "msg": "corpo inválido"})
        rota = urlparse(self.path).path
        if rota == "/api/cmd":
            return self._comandos(corpo)
        if rota == "/api/parar":
            self._json(200, {"ok": True, "msg": "servidor parando"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        return self._json(404, {"ok": False, "msg": "não encontrado"})

    def _comandos(self, corpo):
        cmds = corpo.get("cmds")
        if not isinstance(cmds, list) or not cmds or len(cmds) > MAX_COMANDOS \
                or not all(isinstance(c, list) and all(isinstance(a, str) for a in c) for c in cmds):
            return self._json(400, {"ok": False, "msg": f"cmds precisa ser uma lista de 1 a {MAX_COMANDOS} comandos (listas de texto)"})
        try:
            ritmo = min(max(int(corpo.get("ritmo", 0)), 0), MAX_RITMO_MS) / 1000
        except (TypeError, ValueError):
            ritmo = 0
        continuar = bool(corpo.get("continuar"))
        resultados, parou_em = [], None
        for i, cmd in enumerate(cmds):
            with self.server.lock:
                r = self.server.tela.executar(cmd)
            resultados.append(r)
            if not r["ok"] and not continuar:
                parou_em = i
                break
            if ritmo and i < len(cmds) - 1:
                time.sleep(ritmo)
        self._json(200, {"resultados": resultados, "parou_em": parou_em})


def criar_servidor(porta, pasta_dados):
    return Servidor(("127.0.0.1", porta), pasta_dados)


def main():
    ap = argparse.ArgumentParser(description="Servidor do pixel-agente (só em 127.0.0.1)")
    ap.add_argument("--porta", type=int, default=int(os.environ.get("PIXEL_PORTA", PORTA_PADRAO)))
    ap.add_argument("--dados", default=os.path.join(AQUI, "desenhos"), help="pasta dos desenhos salvos")
    ap.add_argument("--abrir", action="store_true", help="abre a janela no navegador")
    args = ap.parse_args()
    try:
        servidor = criar_servidor(args.porta, args.dados)
    except OSError as e:
        print(f"erro: não consegui abrir a porta {args.porta} ({e}). Já tem um pixel-agente rodando?", file=sys.stderr)
        sys.exit(1)
    threading.Thread(target=servidor.laco_autosave, daemon=True).start()
    url = f"http://127.0.0.1:{args.porta}/"
    print(f"pixel-agente {VERSAO} no ar em {url}", flush=True)
    if args.abrir:
        webbrowser.open(url)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.salvar_atual()
        servidor.server_close()


if __name__ == "__main__":
    main()
