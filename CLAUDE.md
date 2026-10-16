# CLAUDE.md: pixel-agente

Regras deste projeto. As da pasta continuam valendo
(`C:\Users\felipe\Downloads\Claude Code\CLAUDE.md`); aqui fica só o que é
específico. Criado em 15/10/2026.

## O que é

Um app de pixel art em que **quem desenha é um agente de IA**, por linha de
comando. O Felipe só acompanha, ao vivo, numa janela no navegador. Cada ação
do app é um comando do `px.py`. Não existe desenhar com o mouse de propósito.

Pincel de **1 pixel**, só isso. Não existem linha, retângulo, balde nem
círculo, por decisão do pedido original. Se um dia quiserem, entram como
comandos novos no `nucleo.py`.

## Como um agente desenha (leia isto antes de usar)

O servidor sobe sozinho na porta **8750** na primeira chamada do `px.py`. Rode
sempre de dentro desta pasta:

```powershell
python px.py ajuda            # lista todos os comandos
python px.py novo 32 32       # resolução (1 a 256 por lado), tela vazia
python px.py cor "#e0314b"    # cor atual (#rgb ou #rrggbb; aspas por causa do #)
python px.py pixel 5 3        # pinta 1 pixel com a cor atual
python px.py pixel 6 3 "#fff" # pinta com essa cor, que vira a atual
python px.py apagar 5 3       # deixa transparente
python px.py desfazer [N]     # desfaz N ações
python px.py mapa             # a tela em texto, uma letra por cor
python px.py ver --grade      # PNG ampliado: imprime o caminho, abra com a ferramenta de ler imagem
python px.py salvar nome      # grava desenhos/nome.json e nome.png
```

Coordenadas: `x` cresce para a direita e `y` para baixo, a partir de 0. O pixel
de cima à esquerda é `0 0`.

**Pixel por pixel é lento demais.** Use `lote`, que roda muitos comandos de uma
vez e deixa 30 ms entre eles para o Felipe conseguir assistir (`--ritmo 0` é
instantâneo, `--ritmo 80` é mais devagar):

```powershell
python px.py lote desenho.txt          # um comando por linha; linha com // no começo é comentário
python px.py lote -c "cor #f00; pixel 1 1; pixel 2 1"
Get-Content desenho.txt | python px.py lote -
```

O `lote` para no primeiro erro e diz qual comando falhou (`--continuar` segue).

**Conferindo o que você desenhou:** `ver` gera o PNG (padrão
`saida/ver.png`, escala automática; `--grade` põe linhas, mais escuras a cada 8
pixels; `--recorte X,Y,L,A` dá zoom numa parte) e `mapa` imprime o desenho em
texto com régua, o que serve até sem ver imagem. **Olhe o resultado de vez em
quando** em vez de confiar nas coordenadas de cabeça: erro de uma coluna
estraga o sprite.

Um jeito que funciona bem: escrever o desenho em arte ASCII (uma letra por
cor), gerar o lote com um scriptzinho e conferir com `ver`. O script é seu, na
pasta temporária; o app em si só conhece pixel.

Códigos de saída do `px.py`: 0 deu certo, 1 comando recusado (mensagem
`erro: ...`), 2 sem servidor.

## Decisões fechadas

- **Servidor em Python puro** (`servidor.py`, só biblioteca padrão), só em
  `127.0.0.1`. Responde só ao `Host` e ao `Origin` do próprio endereço e só
  aceita POST com `Content-Type: application/json`, para uma página de outro
  site não conseguir desenhar. `allow_reuse_address = False` é de propósito: no
  Windows `True` deixa dois servidores abrirem a mesma porta.
- **Toda a lógica está no `nucleo.py`** (`Tela.executar(["pixel","3","4"])`).
  Comando novo = entrada em `COMANDOS` + método `cmd_<nome>`; o `px.py ajuda` e
  a validação de argumentos saem da tabela. Não dá para pular o `nucleo.py`: a
  janela só mostra o que a tela emite como evento.
- **A janela é só leitura.** Recebe por SSE (`/api/eventos`) cada mudança com
  número de sequência; se faltar um número, recarrega tudo por
  `/api/estado`.
- **O desfazer é por ação**, não por pixel: `limpar` desfaz de uma vez; pintar a
  cor que o pixel já tem não entra no desfazer.
- **Autosave**: a tela inteira vai para `desenhos/_atual.json` (fora do git) a
  cada segundo em que mudou e ao desligar; volta sozinha na próxima vez. Nome
  de desenho salvo não pode começar com `_`.
- **Limite de 256 por lado** (a tela inteira vai a cada atualização completa).
- O PNG é gerado à mão com `zlib` e `struct`, sem Pillow, de propósito (zero
  dependência).
- O `px.py` sobe o servidor sozinho (processo solto, sem janela). Quem terminar
  de usar pode derrubar com `python px.py parar`. `PIXEL_PORTA` troca a porta e
  `PIXEL_SEM_AUTOSTART=1` desliga o auto-início (os testes usam).

## Como rodar e testar

- Uso: `abrir.bat` (sobe o servidor e abre a janela) ou `python px.py janela`.
- Preview do Claude Code: entrada `pixel-agente` do `launch.json` da raiz
  (porta 8750).
- Testes: `python -m unittest discover -s testes -v` (núcleo, servidor, SSE,
  proteções e o `px.py` de verdade, tudo numa pasta temporária).
- Janela: conferida em 1024×768 e 1080×1920. Tela estreita ou retrato põe o
  painel embaixo.

## Versão

1.0.0 (15/10/2026). A versão mora em `servidor.py` (`VERSAO`) e no
`CHANGELOG.md`, sempre iguais.
