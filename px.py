#!/usr/bin/env python
"""px: o comando do pixel-agente. Cada ação do app é um subcomando. `python px.py ajuda` lista todos."""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from nucleo import COMANDOS

AQUI = Path(__file__).resolve().parent
PORTA = int(os.environ.get("PIXEL_PORTA", "8750"))
BASE = f"http://127.0.0.1:{PORTA}"
RITMO_PADRAO_LOTE = 30


class ErroPx(Exception):
    def __init__(self, msg, codigo=1):
        super().__init__(msg)
        self.codigo = codigo


def requisicao(caminho, corpo=None, timeout=30):
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(BASE + caminho, data=dados, headers={"Content-Type": "application/json"} if dados else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("msg", str(e))
        except ValueError:
            msg = str(e)
        raise ErroPx(f"erro: {msg}") from None


def servidor_no_ar():
    try:
        urllib.request.urlopen(BASE + "/api/estado", timeout=1.5).read()
        return True
    except (urllib.error.URLError, OSError):
        return False


def garantir_servidor():
    if servidor_no_ar():
        return
    if os.environ.get("PIXEL_SEM_AUTOSTART"):
        raise ErroPx(f"erro: não tem servidor na porta {PORTA}", 2)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([sys.executable, str(AQUI / "servidor.py"), "--porta", str(PORTA)], cwd=AQUI,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags)
    for _ in range(40):
        time.sleep(0.15)
        if servidor_no_ar():
            return
    raise ErroPx(f"erro: o servidor não subiu na porta {PORTA}. Rode `python servidor.py` para ver o motivo.", 2)


def enviar(cmds, ritmo=0, continuar=False):
    timeout = 30 + len(cmds) * (ritmo / 1000) * 1.5
    corpo, _ = requisicao("/api/cmd", {"cmds": cmds, "ritmo": ritmo, "continuar": continuar}, timeout)
    return json.loads(corpo)


def comando_simples(argv):
    r = enviar([argv])["resultados"][0]
    print(("ok " if r["ok"] else "erro: ") + r["msg"] if "\n" not in r["msg"] else r["msg"])
    return 0 if r["ok"] else 1


def linhas_do_lote(args):
    if args.c is not None:
        texto = args.c
        pedacos = [p for linha in texto.splitlines() for p in linha.split(";")]
    elif args.arquivo == "-":
        pedacos = sys.stdin.read().splitlines()
    elif args.arquivo:
        try:
            pedacos = Path(args.arquivo).read_text(encoding="utf-8-sig").splitlines()
        except OSError as e:
            raise ErroPx(f"erro: não consegui ler {args.arquivo} ({e})") from None
    else:
        raise ErroPx("uso: lote ARQUIVO | lote - (lê do stdin) | lote -c \"cmd; cmd\"")
    cmds = []
    for p in pedacos:
        p = p.strip()
        if p and not p.startswith("//"):
            try:
                cmds.append(shlex.split(p))
            except ValueError as e:
                raise ErroPx(f"erro: linha com aspas quebradas: {p!r} ({e})") from None
    if not cmds:
        raise ErroPx("erro: o lote está vazio")
    return cmds


def cmd_lote(argv):
    ap = argparse.ArgumentParser(prog="px.py lote", add_help=False)
    ap.add_argument("arquivo", nargs="?")
    ap.add_argument("-c")
    ap.add_argument("--ritmo", type=int, default=RITMO_PADRAO_LOTE)
    ap.add_argument("--continuar", action="store_true")
    ap.add_argument("--verboso", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        raise ErroPx("uso: lote ARQUIVO|- [--ritmo MS] [--continuar] [--verboso] | lote -c \"cmd; cmd\"") from None
    cmds = linhas_do_lote(args)
    resp = enviar(cmds, args.ritmo, args.continuar)
    res = resp["resultados"]
    falhas = [(i, r) for i, r in enumerate(res) if not r["ok"]]
    if args.verboso:
        for r in res:
            print(("ok " if r["ok"] else "erro: ") + r["msg"])
    for i, r in falhas:
        print(f"erro no comando {i + 1} ({' '.join(cmds[i])}): {r['msg']}")
    if resp["parou_em"] is not None:
        print(f"lote parou: {resp['parou_em']} comando(s) feitos, {len(cmds) - resp['parou_em']} não rodaram")
        return 1
    print(f"ok lote: {len(res) - len(falhas)} comandos executados" + (f", {len(falhas)} com erro" if falhas else ""))
    return 1 if falhas else 0


def cmd_ver(argv):
    ap = argparse.ArgumentParser(prog="px.py ver", add_help=False)
    ap.add_argument("--escala", type=int)
    ap.add_argument("--grade", action="store_true")
    ap.add_argument("--recorte")
    ap.add_argument("--saida")
    try:
        args = ap.parse_args(argv)
    except SystemExit:
        raise ErroPx("uso: ver [--escala N] [--grade] [--recorte X,Y,L,A] [--saida arquivo.png]") from None
    q = []
    if args.escala is not None:
        q.append(f"escala={args.escala}")
    if args.grade:
        q.append("grade=1")
    if args.recorte:
        q.append(f"recorte={args.recorte}")
    png, cab = requisicao("/api/png" + ("?" + "&".join(q) if q else ""))
    destino = Path(args.saida) if args.saida else AQUI / "saida" / "ver.png"
    destino = destino.resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(png)
    print(f"ok imagem {cab.get('X-Largura')}x{cab.get('X-Altura')}px salva em {destino}")
    return 0


def cmd_ajuda():
    print("pixel-agente: cada ação é um comando. Uso: python px.py COMANDO [argumentos]\n")
    for nome, (_, _, uso, desc) in COMANDOS.items():
        print(f"  {uso:<30} {desc}")
    print(f"  {'ver [--escala N] [--grade]':<30} gera um PNG ampliado para você olhar (imprime o caminho)")
    print(f"  {'     [--recorte X,Y,L,A] [--saida f]'}")
    print(f"  {'lote ARQUIVO | - | -c \"a; b\"':<30} vários comandos de uma vez ({RITMO_PADRAO_LOTE} ms entre eles; --ritmo 0 é instantâneo)")
    print(f"  {'janela':<30} abre a janela de acompanhamento no navegador")
    print(f"  {'parar':<30} desliga o servidor (a tela fica salva e volta na próxima vez)")
    print(f"\nO servidor sobe sozinho na porta {PORTA} se não estiver no ar. Coordenadas: x vai para a direita, y para baixo, a partir de 0.")
    return 0


def principal(argv):
    if not argv or argv[0] in ("ajuda", "-h", "--help", "help"):
        return cmd_ajuda()
    nome, resto = argv[0].lower(), argv[1:]
    if nome == "parar":
        if not servidor_no_ar():
            print("o servidor já estava parado")
            return 0
        requisicao("/api/parar", {})
        print("ok servidor parado")
        return 0
    garantir_servidor()
    if nome == "janela":
        webbrowser.open(BASE + "/")
        print(f"ok janela aberta em {BASE}/")
        return 0
    if nome == "ver":
        return cmd_ver(resto)
    if nome == "lote":
        return cmd_lote(resto)
    return comando_simples(argv)


def main():
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    try:
        sys.exit(principal(sys.argv[1:]))
    except ErroPx as e:
        print(e, file=sys.stderr)
        sys.exit(e.codigo)
    except (urllib.error.URLError, OSError) as e:
        print(f"erro: falha de conexão com o servidor ({e})", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
