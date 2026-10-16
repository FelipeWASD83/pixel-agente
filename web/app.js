'use strict';

const MARGEM = 24;
const $ = (s) => document.querySelector(s);
const palco = $('#palco'), base = $('#base');
const cvTela = $('#tela'), ctx = cvTela.getContext('2d');
const cvGrade = $('#grade'), gctx = cvGrade.getContext('2d');
const cvRegua = $('#regua'), rctx = cvRegua.getContext('2d');
const ultimo = $('#ultimo');

const estado = { largura: 0, altura: 0, cor: '#000000', pixels: [], seq: 0, pintados: 0 };
let escala = 1;
let carregando = false;
let buffer = [];
let marcaUltimo = null;

// ---------- desenho ----------

function ajustarEscala() {
  const estilo = getComputedStyle(palco);
  const lw = palco.clientWidth - parseFloat(estilo.paddingLeft) - parseFloat(estilo.paddingRight) - MARGEM;
  const lh = palco.clientHeight - parseFloat(estilo.paddingTop) - parseFloat(estilo.paddingBottom) - MARGEM;
  escala = Math.max(1, Math.floor(Math.min(lw / estado.largura, lh / estado.altura)));
}

function redesenharTudo() {
  if (!estado.largura) return;
  ajustarEscala();
  const w = estado.largura * escala, h = estado.altura * escala;
  base.style.width = (w + MARGEM) + 'px';
  base.style.height = (h + MARGEM) + 'px';
  cvTela.width = w; cvTela.height = h;
  cvTela.style.marginLeft = cvTela.style.marginTop = MARGEM + 'px';
  cvTela.style.background = `repeating-conic-gradient(#dedede 0% 25%, #f7f7f7 0% 50%) 0 0 / ${2 * escala}px ${2 * escala}px`;
  for (const c of [cvGrade, cvRegua]) { c.width = w + MARGEM; c.height = h + MARGEM; }
  cvGrade.style.left = '0'; cvGrade.style.top = '0';
  for (let y = 0; y < estado.altura; y++) {
    for (let x = 0; x < estado.largura; x++) {
      const c = estado.pixels[y * estado.largura + x];
      if (c) { ctx.fillStyle = c; ctx.fillRect(x * escala, y * escala, escala, escala); }
    }
  }
  desenharGrade();
  desenharRegua();
  posicionarUltimo();
}

function desenharCelula(x, y) {
  const c = estado.pixels[y * estado.largura + x];
  ctx.clearRect(x * escala, y * escala, escala, escala);
  if (c) { ctx.fillStyle = c; ctx.fillRect(x * escala, y * escala, escala, escala); }
}

function desenharGrade() {
  gctx.clearRect(0, 0, cvGrade.width, cvGrade.height);
  if (!$('#chk-grade').checked || escala < 4) return;
  const w = estado.largura * escala, h = estado.altura * escala;
  for (let i = 0; i <= estado.largura; i++) {
    gctx.fillStyle = i % 8 === 0 ? 'rgba(0,0,0,.55)' : 'rgba(0,0,0,.2)';
    gctx.fillRect(MARGEM + i * escala - (i === estado.largura ? 1 : 0), MARGEM, 1, h);
  }
  for (let j = 0; j <= estado.altura; j++) {
    gctx.fillStyle = j % 8 === 0 ? 'rgba(0,0,0,.55)' : 'rgba(0,0,0,.2)';
    gctx.fillRect(MARGEM, MARGEM + j * escala - (j === estado.altura ? 1 : 0), w, 1);
  }
}

function desenharRegua() {
  rctx.clearRect(0, 0, cvRegua.width, cvRegua.height);
  if (!$('#chk-regua').checked) return;
  const passos = [1, 2, 5, 10, 20, 50];
  const passo = passos.find((p) => p * escala >= 22) || 50;
  rctx.fillStyle = '#c9ccd2';
  rctx.font = '10px Consolas, monospace';
  rctx.textBaseline = 'middle';
  rctx.textAlign = 'center';
  for (let x = 0; x < estado.largura; x += passo) rctx.fillText(x, MARGEM + (x + 0.5) * escala, MARGEM / 2);
  rctx.textAlign = 'right';
  for (let y = 0; y < estado.altura; y += passo) rctx.fillText(y, MARGEM - 4, MARGEM + (y + 0.5) * escala);
}

function posicionarUltimo() {
  if (!marcaUltimo) { ultimo.hidden = true; return; }
  const [x, y] = marcaUltimo;
  const lado = Math.max(escala, 8);
  ultimo.style.width = ultimo.style.height = lado + 'px';
  ultimo.style.left = (MARGEM + (x + 0.5) * escala - lado / 2) + 'px';
  ultimo.style.top = (MARGEM + (y + 0.5) * escala - lado / 2) + 'px';
  ultimo.hidden = false;
}

// ---------- painel ----------

function atualizarPainel() {
  $('#resolucao').textContent = `${estado.largura} × ${estado.altura}`;
  $('#cor').textContent = estado.cor;
  $('#amostra').style.background = estado.cor;
  $('#pintados').textContent = estado.pintados;
}

function adicionarLog(e, aoFim) {
  const li = document.createElement('li');
  if (e.nivel && e.nivel !== 'info') li.className = e.nivel;
  const t = document.createElement('span');
  t.className = 't';
  t.textContent = e.t;
  li.append(t, document.createTextNode(e.msg));
  const lista = $('#log');
  if (aoFim) lista.append(li); else lista.prepend(li);
  while (lista.children.length > 200) lista.lastChild.remove();
}

// ---------- estado e eventos ----------

async function recarregar() {
  carregando = true;
  try {
    const r = await fetch('/api/estado', { cache: 'no-store' });
    const e = await r.json();
    Object.assign(estado, { largura: e.largura, altura: e.altura, cor: e.cor, pixels: e.pixels, seq: e.seq, pintados: e.pintados });
    $('#versao').textContent = 'v' + e.versao;
    $('#log').textContent = '';
    for (const l of e.log) adicionarLog(l, false);
    marcaUltimo = null;
    redesenharTudo();
    atualizarPainel();
  } finally {
    carregando = false;
  }
  const pendentes = buffer;
  buffer = [];
  for (const ev of pendentes) if (ev.seq > estado.seq) aplicar(ev);
}

function aplicar(ev) {
  if (carregando) { buffer.push(ev); return; }
  if (ev.seq <= estado.seq) return;
  if (ev.seq !== estado.seq + 1) { buffer.push(ev); recarregar(); return; }
  estado.seq = ev.seq;
  if (ev.tipo === 'estado') { recarregar(); return; }
  if (ev.cor) estado.cor = ev.cor;
  if (ev.tipo === 'pixels') {
    for (const [x, y, c] of ev.mudancas) {
      const i = y * estado.largura + x;
      estado.pintados += (c ? 1 : 0) - (estado.pixels[i] ? 1 : 0);
      estado.pixels[i] = c;
      desenharCelula(x, y);
    }
    marcaUltimo = ev.ultimo;
    posicionarUltimo();
  }
  atualizarPainel();
  adicionarLog(ev, false);
}

function conectar() {
  const fonte = new EventSource('/api/eventos');
  fonte.onopen = () => {
    const c = $('#conexao');
    c.textContent = 'conectado'; c.className = 'conectado';
    recarregar();
  };
  fonte.onmessage = (m) => aplicar(JSON.parse(m.data));
  fonte.onerror = () => {
    const c = $('#conexao');
    c.textContent = 'sem conexão com o servidor… tentando de novo'; c.className = 'desconectado';
  };
}

// ---------- interação ----------

cvTela.addEventListener('mousemove', (e) => {
  const x = Math.floor(e.offsetX / escala), y = Math.floor(e.offsetY / escala);
  const c = estado.pixels[y * estado.largura + x];
  $('#mouse').textContent = `(${x}, ${y}) ${c || 'vazio'}`;
});
cvTela.addEventListener('mouseleave', () => { $('#mouse').textContent = '–'; });
$('#chk-grade').addEventListener('change', desenharGrade);
$('#chk-regua').addEventListener('change', desenharRegua);
window.addEventListener('resize', redesenharTudo);

conectar();
