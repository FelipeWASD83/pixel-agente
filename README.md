# Pixel Agente

Um app de pixel art em que quem desenha é a IA. Você escolhe a resolução, o
agente escolhe as cores e pinta um pixel de cada vez por comandos, e você
assiste tudo em tempo real numa janela.

## Como usar

1. Dê dois cliques em `abrir.bat`. Ele liga o servidor e abre a janela no
   navegador (endereço `http://127.0.0.1:8750/`).
2. Peça para o agente desenhar. Quem manda os comandos é o agente, por
   `python px.py ...` dentro desta pasta; o `CLAUDE.md` ensina ele a usar.
   Exemplo de pedido: "desenha um cogumelo 16x16 no pixel-agente".
3. A janela mostra o desenho crescendo, o último pixel piscando, a cor atual e
   a lista dos comandos que chegaram. Dá para ligar e desligar a grade e os
   números das bordas.

Os desenhos salvos ficam na pasta `desenhos` (um `.json` para reabrir e um
`.png` para usar em qualquer lugar). A tela atual é lembrada sozinha: se você
fechar o servidor e abrir de novo, ela volta como estava.

Para desligar o servidor: `python px.py parar`.

## Se algo não funcionar

- "já tem um pixel-agente rodando": a porta 8750 está ocupada, provavelmente
  pelo próprio app. Use a janela que já está aberta ou rode `python px.py parar`.
- A janela diz "sem conexão": o servidor caiu. Rode `abrir.bat` de novo.
