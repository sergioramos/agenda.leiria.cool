/* Admin panel: soft login gate (account set by the user, stored hashed in this
   browser only), GitHub connection settings, on-demand crawl triggers, and the
   proposed-changes review. No password is ever hard-coded or sent anywhere. */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const el = (t, p = {}, ...k) => { const n = Object.assign(document.createElement(t), p); k.forEach(c => n.append(c)); return n; };

const ACCOUNT_KEY = 'le_admin_account';
const SESSION_KEY = 'le_admin_session';
const CFG_KEY = 'le_apply_cfg';
const WF_EVENTS = 'crawl-events.yml';
const WF_SOURCES = 'check-sources.yml';

const STATUS_PT = {
  active: 'ativo', closed: 'fechado', closing: 'a fechar', at_risk: 'em risco',
  possibly_closed: 'possivelmente fechado', renovation: 'em obras',
  relocated: 'mudou de local', not_running: 'não se realiza',
};

const decisions = { closures: {}, new_venues: {} };
let proposed = null;

/* ---------- crypto + account ---------- */
async function sha256hex(str) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  return [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
}
function randSalt() {
  return [...crypto.getRandomValues(new Uint8Array(16))].map(b => b.toString(16).padStart(2, '0')).join('');
}
const getAccount = () => JSON.parse(localStorage.getItem(ACCOUNT_KEY) || 'null');
const setAccount = (a) => localStorage.setItem(ACCOUNT_KEY, JSON.stringify(a));

/* ---------- gate ---------- */
function showGate() {
  $('#dash').hidden = true;
  $('#gate').hidden = false;
  $('#session-actions').hidden = true;
  const setup = !getAccount();
  $('#gate-title').textContent = setup ? 'Criar conta' : 'Entrar';
  $('#gate-sub').textContent = setup
    ? 'Primeira utilização: defina o utilizador e a palavra-passe deste painel. Ficam guardados (cifrados) só neste navegador.'
    : 'Introduza as suas credenciais.';
  $('#g-confirm-wrap').hidden = !setup;
  $('#gate-submit').textContent = setup ? 'Criar conta e entrar' : 'Entrar';
  $('#gate-reset-wrap').hidden = setup;
  $('#gate-msg').textContent = '';
}

async function handleGate(e) {
  e.preventDefault();
  const setup = !getAccount();
  const user = $('#g-user').value.trim();
  const pass = $('#g-pass').value;
  const msg = $('#gate-msg');
  if (!user || !pass) { msg.textContent = 'Preencha utilizador e palavra-passe.'; return; }

  if (setup) {
    if (pass.length < 6) { msg.textContent = 'Use uma palavra-passe com pelo menos 6 caracteres.'; return; }
    if (pass !== $('#g-confirm').value) { msg.textContent = 'As palavras-passe não coincidem.'; return; }
    const salt = randSalt();
    setAccount({ user, salt, hash: await sha256hex(salt + pass) });
    enterDash();
  } else {
    const acc = getAccount();
    const ok = user === acc.user && (await sha256hex(acc.salt + pass)) === acc.hash;
    if (!ok) { msg.textContent = 'Credenciais inválidas.'; return; }
    enterDash();
  }
}

function enterDash() {
  sessionStorage.setItem(SESSION_KEY, '1');
  $('#gate').hidden = true;
  $('#dash').hidden = false;
  $('#session-actions').hidden = false;
  loadConfig();
  loadProposed();
}

/* ---------- settings ---------- */
const getCfg = () => JSON.parse(localStorage.getItem(CFG_KEY) || 'null') || {};
function loadConfig() {
  const c = getCfg();
  $('#cfg-repo').value = c.repo || '';
  $('#cfg-token').value = c.token || '';
  $('#cfg-status').textContent = c.repo ? 'ligado a ' + c.repo : 'por configurar';
}
function saveConfig() {
  const repo = $('#cfg-repo').value.trim().replace(/^https?:\/\/github\.com\//, '').replace(/\/$/, '');
  const token = $('#cfg-token').value.trim();
  localStorage.setItem(CFG_KEY, JSON.stringify({ repo, token, branch: 'main' }));
  $('#cfg-status').textContent = repo ? 'guardado · ' + repo : 'guardado';
}

/* ---------- GitHub calls ---------- */
function gh(path, opts = {}) {
  const c = getCfg();
  return fetch('https://api.github.com' + path, {
    ...opts,
    headers: {
      Authorization: 'Bearer ' + c.token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(opts.headers || {}),
    },
  });
}

async function runWorkflow(file, label) {
  const c = getCfg();
  const status = $('#run-status');
  if (!c.repo || !c.token) { status.textContent = 'Configure o repositório e o token primeiro (em Definições).'; return; }
  status.textContent = `A pedir: ${label}…`;
  try {
    const res = await gh(`/repos/${c.repo}/actions/workflows/${file}/dispatches`, {
      method: 'POST', body: JSON.stringify({ ref: c.branch || 'main' }),
    });
    if (res.status === 204) status.textContent = `✓ ${label}: pedido enviado. Veja o progresso no separador Actions do repositório.`;
    else if (res.status === 404) status.textContent = `O workflow "${file}" ainda não existe no repositório (ou o token não vê o repo).`;
    else if (res.status === 401 || res.status === 403) status.textContent = 'Token inválido ou sem permissão Actions: write.';
    else status.textContent = `Resposta inesperada do GitHub (${res.status}).`;
  } catch (e) { status.textContent = 'Erro de rede: ' + e.message; }
}

/* ---------- proposed changes ---------- */
async function loadProposed() {
  try { proposed = await (await fetch('../data/proposed-changes/latest.json', { cache: 'no-cache' })).json(); }
  catch { proposed = { closures: [], new_venues: [] }; }
  renderProposed();
}

function changeCard(kind, key, title, sub, badge) {
  const cb = el('input', { type: 'checkbox', id: `${kind}-${key}` });
  cb.onchange = () => { decisions[kind][key] = cb.checked; };
  return el('article', { className: 'card' },
    el('div', { className: 'when' }, cb),
    el('div', { className: 'body' },
      el('h3', {}, el('label', { htmlFor: `${kind}-${key}`, textContent: title })),
      el('p', { className: 'meta-line', textContent: sub }),
      el('div', { className: 'badges' }, el('span', { className: 'badge', textContent: badge }))));
}

function renderProposed() {
  $('#changes-gen').textContent = proposed.generated_at
    ? 'Proposto a ' + new Date(proposed.generated_at).toLocaleString('pt-PT')
    : 'Ainda sem propostas — corra "Verificar locais agora".';
  const c = $('#closures'); c.innerHTML = '';
  (proposed.closures || []).forEach(x => c.append(changeCard('closures', x.id, x.name,
    [x.neighbourhood, x.reason].filter(Boolean).join(' · '),
    'estado: ' + (STATUS_PT[x.current_status] || x.current_status || 'ativo'))));
  $('#closures-count').textContent = (proposed.closures || []).length;
  const n = $('#new-venues'); n.innerHTML = '';
  (proposed.new_venues || []).forEach((v, i) => n.append(changeCard('new_venues', String(i), v.name,
    [v.neighbourhood, v.note].filter(Boolean).join(' · '), 'via ' + (v.found_via || 'listagens'))));
  $('#new-count').textContent = (proposed.new_venues || []).length;
}

async function applyChanges() {
  const c = getCfg();
  const payload = {
    generated_at: proposed.generated_at,
    accept_closures: (proposed.closures || []).filter(x => decisions.closures[x.id]),
    accept_new: (proposed.new_venues || []).filter((v, i) => decisions.new_venues[String(i)]),
  };
  const total = payload.accept_closures.length + payload.accept_new.length;
  const status = $('#apply-status');
  if (!total) { status.textContent = 'Marque as alterações que aceita primeiro.'; return; }
  if (c.repo && c.token) {
    status.textContent = 'A enviar para o GitHub…';
    try {
      const res = await gh(`/repos/${c.repo}/dispatches`, {
        method: 'POST', body: JSON.stringify({ event_type: 'apply-changes', client_payload: payload }),
      });
      status.textContent = res.status === 204
        ? `✓ ${total} alteração(ões) enviadas. A lista atualiza em ~1 minuto (separador Actions).`
        : `O GitHub respondeu ${res.status}. Verifique o token (Contents: write).`;
    } catch (e) { status.textContent = 'Erro de rede: ' + e.message; }
  } else {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = el('a', { href: URL.createObjectURL(blob), download: 'accepted-changes.json' });
    document.body.append(a); a.click(); a.remove();
    status.textContent = `Sem token: transferido accepted-changes.json (${total}). Configure o token para aplicar com um clique.`;
  }
}

/* ---------- wire-up ---------- */
$('#gate-form').addEventListener('submit', handleGate);
$('#gate-reset').addEventListener('click', (e) => {
  e.preventDefault();
  if (confirm('Repor a conta de administrador deste navegador? Terá de criar uma nova.')) {
    localStorage.removeItem(ACCOUNT_KEY); sessionStorage.removeItem(SESSION_KEY); showGate();
  }
});
$('#logout').addEventListener('click', () => { sessionStorage.removeItem(SESSION_KEY); location.reload(); });
$('#cfg-save').addEventListener('click', saveConfig);
$('#run-events').addEventListener('click', () => runWorkflow(WF_EVENTS, 'Recolher eventos'));
$('#run-sources').addEventListener('click', () => runWorkflow(WF_SOURCES, 'Verificar locais'));
$('#apply').addEventListener('click', applyChanges);
$('#select-all').addEventListener('click', () => {
  (proposed.closures || []).forEach(x => { decisions.closures[x.id] = true; const b = $(`#closures-${x.id}`); if (b) b.checked = true; });
  (proposed.new_venues || []).forEach((v, i) => { decisions.new_venues[String(i)] = true; const b = $(`#new_venues-${i}`); if (b) b.checked = true; });
});

if (sessionStorage.getItem(SESSION_KEY) === '1' && getAccount()) enterDash();
else showGate();
