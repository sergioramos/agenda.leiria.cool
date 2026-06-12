/* Admin panel. Access is enforced server-side by Vercel Edge Middleware
   (docs/middleware.js). This script powers the dashboard:
   - Estado: last crawl, events, cost per run / per month (from our own data)
   - GitHub connection (repo + fine-grained PAT, stored in this browser only)
   - DeepSeek key: sealed-box encrypted in this browser, stored as a GitHub
     Actions secret via the API (never readable afterwards), + balance check
   - Spending limits: edits config.yaml in the repo via the Contents API
   - Run-now triggers and the proposed-changes review */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const el = (t, p = {}, ...k) => { const n = Object.assign(document.createElement(t), p); k.forEach(c => n.append(c)); return n; };

const CFG_KEY = 'le_apply_cfg';
const DS_KEY = 'le_ds_key';
const WF_EVENTS = 'crawl-events.yml';
const WF_SOURCES = 'check-sources.yml';

const STATUS_PT = {
  active: 'ativo', closed: 'fechado', closing: 'a fechar', at_risk: 'em risco',
  possibly_closed: 'possivelmente fechado', renovation: 'em obras',
  relocated: 'mudou de local', not_running: 'não se realiza',
};

const decisions = { closures: {}, new_venues: {} };
let proposed = null;

/* ---------- helpers ---------- */
const getCfg = () => JSON.parse(localStorage.getItem(CFG_KEY) || 'null') || {};
const fmtUSD = (v) => '$' + (Math.round(v * 100) / 100).toFixed(2);

function b64encodeUtf8(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  bytes.forEach(b => { bin += String.fromCharCode(b); });
  return btoa(bin);
}
function b64decodeUtf8(b64) {
  const bytes = Uint8Array.from(atob(b64.replace(/\s/g, '')), c => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

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
const hasGh = () => { const c = getCfg(); return !!(c.repo && c.token); };

/* ---------- settings: github ---------- */
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
  refreshSecretState();
  loadLimits();
}

/* ---------- estado ---------- */
async function loadStatus() {
  try {
    const idx = await (await fetch('/data/weeks/index.json', { cache: 'no-cache' })).json();
    const weeks = (idx.weeks || []).slice().sort((a, b) => b.start.localeCompare(a.start));
    if (!weeks.length) return;
    const latest = weeks[0];
    const gen = new Date(latest.generated_at);
    $('#st-last').textContent = gen.toLocaleDateString('pt-PT') + (latest.is_sample ? ' (exemplo)' : '');
    $('#st-events').textContent = latest.event_count;

    const month = new Date().toISOString().slice(0, 7);
    let monthCost = 0, lastCost = null;
    for (const w of weeks) {
      if (w.is_sample) continue;
      const inMonth = String(w.generated_at || '').slice(0, 7) === month;
      if (!inMonth && lastCost !== null) continue;
      try {
        const data = await (await fetch('/data/weeks/' + w.file, { cache: 'no-cache' })).json();
        const cost = (data.meta && data.meta.ai_cost_usd) || 0;
        if (lastCost === null) lastCost = cost;
        if (inMonth) monthCost += cost;
      } catch { /* ignore one bad file */ }
    }
    $('#st-last-cost').textContent = lastCost === null ? '—' : fmtUSD(lastCost);
    $('#st-month-cost').textContent = fmtUSD(monthCost);
  } catch {
    $('#st-note').textContent = 'Não foi possível ler os dados das recolhas.';
  }
}

/* ---------- deepseek: secret + balance ---------- */
async function refreshSecretState() {
  const state = $('#ds-secret-state');
  if (!hasGh()) { state.textContent = 'Configure o GitHub (passo 1) para gerir a chave.'; return; }
  const c = getCfg();
  try {
    const res = await gh(`/repos/${c.repo}/actions/secrets/DEEPSEEK_API_KEY`);
    if (res.status === 200) {
      const s = await res.json();
      state.textContent = '✓ Chave configurada no GitHub (atualizada a ' +
        new Date(s.updated_at || s.created_at).toLocaleDateString('pt-PT') + ').';
    } else if (res.status === 404) {
      state.textContent = 'Ainda não há chave configurada no GitHub — cole-a acima e clique «Guardar no GitHub».';
    } else if (res.status === 403) {
      state.textContent = 'O token não tem a permissão Secrets (Read and write) — atualize o token no GitHub.';
    } else {
      state.textContent = `Não foi possível verificar (GitHub respondeu ${res.status}).`;
    }
  } catch (e) { state.textContent = 'Erro de rede ao verificar a chave: ' + e.message; }
}

async function saveDeepseekKey() {
  const status = $('#ds-status');
  const key = $('#ds-key').value.trim();
  if (!key) { status.textContent = 'Cole a chave primeiro.'; return; }
  if (!hasGh()) { status.textContent = 'Configure o GitHub (passo 1) primeiro.'; return; }
  const c = getCfg();
  status.textContent = 'A cifrar e guardar…';
  try {
    const pkRes = await gh(`/repos/${c.repo}/actions/secrets/public-key`);
    if (!pkRes.ok) {
      status.textContent = pkRes.status === 403
        ? 'O token não tem a permissão Secrets (Read and write).'
        : `GitHub respondeu ${pkRes.status} ao pedir a chave pública.`;
      return;
    }
    const pk = await pkRes.json();
    await window.sodium.ready;
    const s = window.sodium;
    const sealed = s.crypto_box_seal(s.from_string(key), s.from_base64(pk.key, s.base64_variants.ORIGINAL));
    const encrypted = s.to_base64(sealed, s.base64_variants.ORIGINAL);
    const putRes = await gh(`/repos/${c.repo}/actions/secrets/DEEPSEEK_API_KEY`, {
      method: 'PUT', body: JSON.stringify({ encrypted_value: encrypted, key_id: pk.key_id }),
    });
    if (putRes.status === 201 || putRes.status === 204) {
      localStorage.setItem(DS_KEY, key); // kept locally only for the balance check
      $('#ds-key').value = '';
      status.textContent = '✓ Chave guardada como segredo do repositório. As próximas recolhas já a usam.';
      refreshSecretState();
    } else {
      status.textContent = `GitHub respondeu ${putRes.status} ao guardar o segredo.`;
    }
  } catch (e) { status.textContent = 'Erro: ' + e.message; }
}

async function checkBalance() {
  const status = $('#ds-status');
  const key = $('#ds-key').value.trim() || localStorage.getItem(DS_KEY) || '';
  if (!key) { status.textContent = 'Cole a chave (ou guarde-a primeiro) para consultar o saldo.'; return; }
  status.textContent = 'A consultar o saldo…';
  try {
    const res = await fetch('https://api.deepseek.com/user/balance', {
      headers: { Authorization: 'Bearer ' + key },
    });
    if (!res.ok) { status.textContent = `A DeepSeek respondeu ${res.status} — confirme a chave.`; return; }
    const data = await res.json();
    const info = (data.balance_infos || [])[0];
    status.textContent = info
      ? `Saldo DeepSeek: ${info.total_balance} ${info.currency}`
      : 'Resposta sem informação de saldo.';
  } catch {
    status.textContent = 'O navegador não conseguiu contactar a DeepSeek (restrição CORS). ' +
      'Veja o saldo em platform.deepseek.com.';
  }
}

/* ---------- spending limits (edits config.yaml in the repo) ---------- */
async function fetchConfigYaml() {
  const c = getCfg();
  if (hasGh()) {
    const res = await gh(`/repos/${c.repo}/contents/config.yaml?ref=${c.branch || 'main'}`);
    if (res.ok) {
      const data = await res.json();
      return { text: b64decodeUtf8(data.content), sha: data.sha };
    }
  }
  if (c.repo) {
    const res = await fetch(`https://raw.githubusercontent.com/${c.repo}/main/config.yaml`, { cache: 'no-cache' });
    if (res.ok) return { text: await res.text(), sha: null };
  }
  return null;
}

function parseLimit(text, name) {
  const m = text.match(new RegExp(`${name}:\\s*([0-9.]+)`));
  return m ? parseFloat(m[1]) : null;
}

async function loadLimits() {
  try {
    const cfg = await fetchConfigYaml();
    if (!cfg) { $('#lim-status').textContent = 'Configure o GitHub (passo 1) para ler os limites.'; return; }
    const run = parseLimit(cfg.text, 'max_run_cost_usd');
    const month = parseLimit(cfg.text, 'max_month_cost_usd');
    if (run !== null) $('#lim-run').value = run;
    if (month !== null) $('#lim-month').value = month;
    $('#lim-status').textContent = 'limites atuais carregados do repositório';
    $('#st-note').textContent = (run !== null && month !== null)
      ? `Limites em vigor: ${fmtUSD(run)} por recolha · ${fmtUSD(month)} por mês.` : '';
  } catch { $('#lim-status').textContent = 'Não foi possível ler config.yaml.'; }
}

async function saveLimits() {
  const status = $('#lim-status');
  if (!hasGh()) { status.textContent = 'Configure o GitHub (passo 1) primeiro.'; return; }
  const run = parseFloat($('#lim-run').value);
  const month = parseFloat($('#lim-month').value);
  if (!(run >= 0) || !(month >= 0)) { status.textContent = 'Valores inválidos — use números (ex.: 2 e 8).'; return; }
  const c = getCfg();
  status.textContent = 'A guardar no repositório…';
  try {
    const res = await gh(`/repos/${c.repo}/contents/config.yaml?ref=${c.branch || 'main'}`);
    if (!res.ok) { status.textContent = `GitHub respondeu ${res.status} ao ler config.yaml.`; return; }
    const data = await res.json();
    let text = b64decodeUtf8(data.content);
    text = text.replace(/(max_run_cost_usd:\s*)[0-9.]+/, `$1${run}`);
    text = text.replace(/(max_month_cost_usd:\s*)[0-9.]+/, `$1${month}`);
    const put = await gh(`/repos/${c.repo}/contents/config.yaml`, {
      method: 'PUT',
      body: JSON.stringify({
        message: `config: limites de custo via admin (${run}/recolha, ${month}/mês)`,
        content: b64encodeUtf8(text),
        sha: data.sha,
        branch: c.branch || 'main',
      }),
    });
    if (put.ok) {
      status.textContent = `✓ Limites guardados: ${fmtUSD(run)} por recolha · ${fmtUSD(month)} por mês.`;
      $('#st-note').textContent = `Limites em vigor: ${fmtUSD(run)} por recolha · ${fmtUSD(month)} por mês.`;
    } else {
      status.textContent = put.status === 403
        ? 'O token não tem a permissão Contents (Read and write).'
        : `GitHub respondeu ${put.status} ao guardar.`;
    }
  } catch (e) { status.textContent = 'Erro: ' + e.message; }
}

/* ---------- run-now ---------- */
async function runWorkflow(file, label) {
  const c = getCfg();
  const status = $('#run-status');
  if (!hasGh()) { status.textContent = 'Configure o repositório e o token primeiro (passo 1).'; return; }
  status.textContent = `A pedir: ${label}…`;
  try {
    const res = await gh(`/repos/${c.repo}/actions/workflows/${file}/dispatches`, {
      method: 'POST', body: JSON.stringify({ ref: c.branch || 'main' }),
    });
    if (res.status === 204) status.textContent = `✓ ${label}: pedido enviado. Veja o progresso no separador Actions do repositório.`;
    else if (res.status === 404) status.textContent = `O workflow «${file}» ainda não existe no repositório (ou o token não vê o repo).`;
    else if (res.status === 401 || res.status === 403) status.textContent = 'Token inválido ou sem permissão Actions: write.';
    else status.textContent = `Resposta inesperada do GitHub (${res.status}).`;
  } catch (e) { status.textContent = 'Erro de rede: ' + e.message; }
}

/* ---------- proposed changes ---------- */
async function loadProposed() {
  try { proposed = await (await fetch('/data/proposed-changes/latest.json', { cache: 'no-cache' })).json(); }
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
    : 'Ainda sem propostas — corra «Verificar locais agora».';
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
  if (hasGh()) {
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
    status.textContent = `Sem token: transferido accepted-changes.json (${total}). Configure o GitHub para aplicar com um clique.`;
  }
}

/* ---------- wire-up ---------- */
$('#cfg-save').addEventListener('click', saveConfig);
$('#ds-save').addEventListener('click', saveDeepseekKey);
$('#ds-balance').addEventListener('click', checkBalance);
$('#lim-save').addEventListener('click', saveLimits);
$('#run-events').addEventListener('click', () => runWorkflow(WF_EVENTS, 'Recolher eventos'));
$('#run-sources').addEventListener('click', () => runWorkflow(WF_SOURCES, 'Verificar locais'));
$('#apply').addEventListener('click', applyChanges);
$('#select-all').addEventListener('click', () => {
  (proposed.closures || []).forEach(x => { decisions.closures[x.id] = true; const b = $(`#closures-${x.id}`); if (b) b.checked = true; });
  (proposed.new_venues || []).forEach((v, i) => { decisions.new_venues[String(i)] = true; const b = $(`#new_venues-${i}`); if (b) b.checked = true; });
});

loadConfig();
loadStatus();
loadProposed();
refreshSecretState();
loadLimits();

/* ---------- announcement-bar ticker (same component as the public site) ---------- */
(function initTicker() {
  const track = document.querySelector('.ticker-track');
  if (!track || typeof buildTicker !== 'function') return;
  const seg = () => el('span', { className: 'ticker-item',
    textContent: 'Pregoeiro · Painel de administração · Acesso restrito' });
  const rebuild = () => buildTicker(track, seg);
  rebuild();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(rebuild);
  let t;
  window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(rebuild, 200); });
})();
