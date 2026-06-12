/* Painel de administração do Pregoeiro.
   Acesso protegido no servidor (middleware.js → /api/auth). Três separadores:
   01 Setup (GitHub, chave DeepSeek, limites) · 02 Ações (recolhas + propostas)
   · 03 Base de dados (CRUD de locais em sources/sources.json e dos eventos da
   semana em docs/data/weeks/*.json, via GitHub Contents API — cada gravação é
   um commit; a Vercel redeploya sozinha). O token fica só neste navegador. */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const el = (t, p = {}, ...k) => { const n = Object.assign(document.createElement(t), p); k.forEach(c => n.append(c)); return n; };

const CFG_KEY = 'le_apply_cfg';
const DS_KEY = 'le_ds_key';
const WF_EVENTS = 'crawl-events.yml';
const WF_SOURCES = 'check-sources.yml';
const SOURCES_PATH = 'sources/sources.json';
const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const DAY_PT = { mon: 'Seg', tue: 'Ter', wed: 'Qua', thu: 'Qui', fri: 'Sex', sat: 'Sáb', sun: 'Dom' };

const STATUS_PT = {
  active: 'ativo', closed: 'fechado', closing: 'a fechar', at_risk: 'em risco',
  possibly_closed: 'possivelmente fechado', renovation: 'em obras',
  relocated: 'mudou de local', not_running: 'não se realiza',
};

const state = {
  taxonomy: null,
  proposed: null,
  decisions: { closures: {}, new_venues: {} },
  src: { list: null, sha: null, limit: 50, editingId: undefined },
  ev: { data: null, sha: null, path: null, limit: 50, editingId: undefined },
};

/* ---------- helpers ---------- */
const getCfg = () => JSON.parse(localStorage.getItem(CFG_KEY) || 'null') || {};
const hasGh = () => { const c = getCfg(); return !!(c.repo && c.token); };
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
function slugify(s) {
  const map = { á: 'a', à: 'a', ã: 'a', â: 'a', é: 'e', ê: 'e', í: 'i', ó: 'o', ô: 'o', õ: 'o', ú: 'u', ç: 'c', '&': 'and' };
  s = (s || '').toLowerCase().replace(/[áàãâéêíóôõúç&]/g, ch => map[ch] || ch);
  return s.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'x';
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

/* Read any repo file (works above the 1MB inline limit): sha from the
   directory listing, content via the raw media type. */
async function ghGetFile(path) {
  const c = getCfg();
  const dir = path.split('/').slice(0, -1).join('/');
  const name = path.split('/').pop();
  const [lsRes, rawRes] = await Promise.all([
    gh(`/repos/${c.repo}/contents/${dir}?ref=main`),
    gh(`/repos/${c.repo}/contents/${path}?ref=main`, { headers: { Accept: 'application/vnd.github.raw+json' } }),
  ]);
  if (!lsRes.ok || !rawRes.ok) throw new Error('GitHub ' + (lsRes.ok ? rawRes.status : lsRes.status));
  const entry = (await lsRes.json()).find(f => f.name === name);
  if (!entry) throw new Error('ficheiro não encontrado: ' + path);
  return { text: await rawRes.text(), sha: entry.sha };
}

async function ghPutFile(path, text, sha, message) {
  const c = getCfg();
  const res = await gh(`/repos/${c.repo}/contents/${path}`, {
    method: 'PUT',
    body: JSON.stringify({ message, content: b64encodeUtf8(text), sha, branch: 'main' }),
  });
  if (!res.ok) {
    if (res.status === 409) throw new Error('o repositório mudou entretanto — recarregue a lista e repita');
    if (res.status === 403) throw new Error('o token não tem a permissão Contents (Read and write)');
    throw new Error('GitHub respondeu ' + res.status);
  }
  return (await res.json()).content.sha;
}

/* ---------- tabs ---------- */
function showTab(name) {
  for (const t of document.querySelectorAll('.tab')) t.setAttribute('aria-selected', t.dataset.tab === name);
  for (const p of document.querySelectorAll('.tab-panel')) p.hidden = p.id !== 'panel-' + name;
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  if (name === 'dados' && !state.src.list) loadDb();
}
function initTabs() {
  document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => showTab(t.dataset.tab)));
  const fromHash = (location.hash || '').slice(1);
  showTab(['setup', 'acoes', 'dados'].includes(fromHash) ? fromHash : 'setup');
}

/* ---------- 01 setup ---------- */
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

async function refreshSecretState() {
  const stateEl = $('#ds-secret-state');
  if (!hasGh()) { stateEl.textContent = 'Configure o GitHub (passo 1) para gerir a chave.'; return; }
  const c = getCfg();
  try {
    const res = await gh(`/repos/${c.repo}/actions/secrets/DEEPSEEK_API_KEY`);
    if (res.status === 200) {
      const s = await res.json();
      stateEl.textContent = '✓ Chave configurada no GitHub (atualizada a ' +
        new Date(s.updated_at || s.created_at).toLocaleDateString('pt-PT') + ').';
    } else if (res.status === 404) {
      stateEl.textContent = 'Ainda não há chave configurada — cole-a acima e clique «Guardar no GitHub».';
    } else if (res.status === 403) {
      stateEl.textContent = 'O token não tem a permissão Secrets (Read and write).';
    } else stateEl.textContent = `Não foi possível verificar (GitHub ${res.status}).`;
  } catch (e) { stateEl.textContent = 'Erro de rede: ' + e.message; }
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
      status.textContent = pkRes.status === 403 ? 'O token não tem a permissão Secrets (Read and write).'
        : `GitHub respondeu ${pkRes.status} ao pedir a chave pública.`;
      return;
    }
    const pk = await pkRes.json();
    await window.sodium.ready;
    const s = window.sodium;
    const sealed = s.crypto_box_seal(s.from_string(key), s.from_base64(pk.key, s.base64_variants.ORIGINAL));
    const putRes = await gh(`/repos/${c.repo}/actions/secrets/DEEPSEEK_API_KEY`, {
      method: 'PUT', body: JSON.stringify({ encrypted_value: s.to_base64(sealed, s.base64_variants.ORIGINAL), key_id: pk.key_id }),
    });
    if (putRes.status === 201 || putRes.status === 204) {
      localStorage.setItem(DS_KEY, key);
      $('#ds-key').value = '';
      status.textContent = '✓ Chave guardada como segredo do repositório.';
      refreshSecretState();
    } else status.textContent = `GitHub respondeu ${putRes.status} ao guardar.`;
  } catch (e) { status.textContent = 'Erro: ' + e.message; }
}

async function checkBalance() {
  const status = $('#ds-status');
  const key = $('#ds-key').value.trim() || localStorage.getItem(DS_KEY) || '';
  if (!key) { status.textContent = 'Cole a chave (ou guarde-a primeiro) para consultar o saldo.'; return; }
  status.textContent = 'A consultar o saldo…';
  try {
    const res = await fetch('https://api.deepseek.com/user/balance', { headers: { Authorization: 'Bearer ' + key } });
    if (!res.ok) { status.textContent = `A DeepSeek respondeu ${res.status} — confirme a chave.`; return; }
    const info = ((await res.json()).balance_infos || [])[0];
    status.textContent = info ? `Saldo DeepSeek: ${info.total_balance} ${info.currency}` : 'Resposta sem saldo.';
  } catch {
    status.textContent = 'O navegador não conseguiu contactar a DeepSeek (CORS). Veja o saldo em platform.deepseek.com.';
  }
}

function parseLimit(text, name) {
  const m = text.match(new RegExp(`${name}:\\s*([0-9.]+)`));
  return m ? parseFloat(m[1]) : null;
}
async function loadLimits() {
  try {
    let text = null;
    const c = getCfg();
    if (hasGh()) text = (await ghGetFile('config.yaml')).text;
    else if (c.repo) {
      const r = await fetch(`https://raw.githubusercontent.com/${c.repo}/main/config.yaml`, { cache: 'no-cache' });
      if (r.ok) text = await r.text();
    }
    if (!text) { $('#lim-status').textContent = 'Configure o GitHub (passo 1) para ler os limites.'; return; }
    const run = parseLimit(text, 'max_run_cost_usd');
    const month = parseLimit(text, 'max_month_cost_usd');
    if (run !== null) $('#lim-run').value = run;
    if (month !== null) $('#lim-month').value = month;
    $('#lim-status').textContent = 'limites atuais carregados do repositório';
    if (run !== null && month !== null) $('#st-note').textContent = `Limites em vigor: ${fmtUSD(run)} por recolha · ${fmtUSD(month)} por mês.`;
  } catch { $('#lim-status').textContent = 'Não foi possível ler config.yaml.'; }
}
async function saveLimits() {
  const status = $('#lim-status');
  if (!hasGh()) { status.textContent = 'Configure o GitHub (passo 1) primeiro.'; return; }
  const run = parseFloat($('#lim-run').value);
  const month = parseFloat($('#lim-month').value);
  if (!(run >= 0) || !(month >= 0)) { status.textContent = 'Valores inválidos — use números (ex.: 2 e 8).'; return; }
  status.textContent = 'A guardar no repositório…';
  try {
    const { text, sha } = await ghGetFile('config.yaml');
    const updated = text
      .replace(/(max_run_cost_usd:\s*)[0-9.]+/, `$1${run}`)
      .replace(/(max_month_cost_usd:\s*)[0-9.]+/, `$1${month}`);
    await ghPutFile('config.yaml', updated, sha, `config: limites de custo via admin (${run}/recolha, ${month}/mes)`);
    status.textContent = `✓ Limites guardados: ${fmtUSD(run)} por recolha · ${fmtUSD(month)} por mês.`;
    $('#st-note').textContent = `Limites em vigor: ${fmtUSD(run)} por recolha · ${fmtUSD(month)} por mês.`;
  } catch (e) { status.textContent = 'Erro: ' + e.message; }
}

/* ---------- estado ---------- */
async function loadStatus() {
  try {
    const idx = await (await fetch('/data/weeks/index.json', { cache: 'no-cache' })).json();
    const weeks = (idx.weeks || []).slice().sort((a, b) => b.start.localeCompare(a.start));
    if (!weeks.length) return;
    const latest = weeks[0];
    $('#st-last').textContent = new Date(latest.generated_at).toLocaleDateString('pt-PT') + (latest.is_sample ? ' (exemplo)' : '');
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
      } catch { /* ignore */ }
    }
    $('#st-last-cost').textContent = lastCost === null ? '—' : fmtUSD(lastCost);
    $('#st-month-cost').textContent = fmtUSD(monthCost);
  } catch { $('#st-note').textContent = 'Não foi possível ler os dados das recolhas.'; }
}

/* ---------- 02 ações ---------- */
async function runWorkflow(file, label) {
  const c = getCfg();
  const status = $('#run-status');
  if (!hasGh()) { status.textContent = 'Configure o repositório e o token primeiro (separador Setup).'; return; }
  status.textContent = `A pedir: ${label}…`;
  try {
    const res = await gh(`/repos/${c.repo}/actions/workflows/${file}/dispatches`, {
      method: 'POST', body: JSON.stringify({ ref: c.branch || 'main' }),
    });
    if (res.status === 204) status.textContent = `✓ ${label}: pedido enviado. Progresso no separador Actions do repositório.`;
    else if (res.status === 404) status.textContent = `O workflow «${file}» não existe no repositório (ou o token não vê o repo).`;
    else if (res.status === 401 || res.status === 403) status.textContent = 'Token inválido ou sem permissão Actions: write.';
    else status.textContent = `GitHub respondeu ${res.status}.`;
  } catch (e) { status.textContent = 'Erro de rede: ' + e.message; }
}

async function loadProposed() {
  try { state.proposed = await (await fetch('/data/proposed-changes/latest.json', { cache: 'no-cache' })).json(); }
  catch { state.proposed = { closures: [], new_venues: [] }; }
  renderProposed();
}
function changeCard(kind, key, title, sub, badge) {
  const cb = el('input', { type: 'checkbox', id: `${kind}-${key}` });
  cb.onchange = () => { state.decisions[kind][key] = cb.checked; };
  return el('article', { className: 'card' },
    el('div', { className: 'when' }, cb),
    el('div', { className: 'body' },
      el('h3', {}, el('label', { htmlFor: `${kind}-${key}`, textContent: title })),
      el('p', { className: 'meta-line', textContent: sub }),
      el('div', { className: 'badges' }, el('span', { className: 'badge', textContent: badge }))));
}
function renderProposed() {
  const p = state.proposed;
  $('#changes-gen').textContent = p.generated_at
    ? 'Proposto a ' + new Date(p.generated_at).toLocaleString('pt-PT')
    : 'Ainda sem propostas — corra «Verificar locais agora».';
  const c = $('#closures'); c.innerHTML = '';
  (p.closures || []).forEach(x => c.append(changeCard('closures', x.id, x.name,
    [x.neighbourhood, x.reason].filter(Boolean).join(' · '),
    'estado: ' + (STATUS_PT[x.current_status] || x.current_status || 'ativo'))));
  $('#closures-count').textContent = (p.closures || []).length;
  const n = $('#new-venues'); n.innerHTML = '';
  (p.new_venues || []).forEach((v, i) => n.append(changeCard('new_venues', String(i), v.name,
    [v.neighbourhood, v.note].filter(Boolean).join(' · '), 'via ' + (v.found_via || 'listagens'))));
  $('#new-count').textContent = (p.new_venues || []).length;
}
async function applyChanges() {
  const c = getCfg();
  const p = state.proposed;
  const payload = {
    generated_at: p.generated_at,
    accept_closures: (p.closures || []).filter(x => state.decisions.closures[x.id]),
    accept_new: (p.new_venues || []).filter((v, i) => state.decisions.new_venues[String(i)]),
  };
  const total = payload.accept_closures.length + payload.accept_new.length;
  const status = $('#apply-status');
  if (!total) { status.textContent = 'Marque as alterações que aceita primeiro.'; return; }
  if (!hasGh()) { status.textContent = 'Configure o GitHub (separador Setup) primeiro.'; return; }
  status.textContent = 'A enviar para o GitHub…';
  try {
    const res = await gh(`/repos/${c.repo}/dispatches`, {
      method: 'POST', body: JSON.stringify({ event_type: 'apply-changes', client_payload: payload }),
    });
    status.textContent = res.status === 204
      ? `✓ ${total} alteração(ões) enviadas. A lista atualiza em ~1 minuto.`
      : `GitHub respondeu ${res.status}. Verifique o token (Contents: write).`;
  } catch (e) { status.textContent = 'Erro de rede: ' + e.message; }
}

/* ---------- 03 base de dados ---------- */
async function loadTaxonomy() {
  if (state.taxonomy) return state.taxonomy;
  state.taxonomy = await (await fetch('/taxonomy.json', { cache: 'no-cache' })).json();
  return state.taxonomy;
}
const topicLabel = (id) => {
  const t = (state.taxonomy?.topics || []).find(x => x.id === id);
  return t ? `${t.emoji} ${t.label}` : id;
};

function fillSelect(sel, options, selected) {
  for (const [value, label] of options) sel.append(el('option', { value, textContent: label, selected: value === selected }));
}

async function loadDb() {
  await loadTaxonomy();
  // populate filter dropdowns once
  if ($('#src-topic').options.length === 1) {
    fillSelect($('#src-topic'), state.taxonomy.topics.map(t => [t.id, t.emoji + ' ' + t.label]));
    fillSelect($('#src-status'), Object.entries(STATUS_PT));
    fillSelect($('#ev-topic'), state.taxonomy.topics.filter(t => !t.is_aggregator).map(t => [t.id, t.emoji + ' ' + t.label]));
  }
  loadSources();
  loadWeekEvents();
}

async function loadSources() {
  const line = $('#src-status-line');
  line.textContent = 'A carregar locais…';
  try {
    const c = getCfg();
    if (hasGh()) {
      const { text, sha } = await ghGetFile(SOURCES_PATH);
      state.src.list = JSON.parse(text).sources;
      state.src.sha = sha;
      line.textContent = '';
    } else if (c.repo) {
      const r = await fetch(`https://raw.githubusercontent.com/${c.repo}/main/${SOURCES_PATH}`, { cache: 'no-cache' });
      state.src.list = (await r.json()).sources;
      state.src.sha = null;
      line.textContent = 'Modo de leitura — configure o GitHub (Setup) para editar.';
    } else {
      line.textContent = 'Configure o repositório no separador Setup para carregar os locais.';
      return;
    }
    renderSources();
  } catch (e) { line.textContent = 'Erro a carregar locais: ' + e.message; }
}

function srcMatches(s, q, topic, status) {
  if (topic && s.topic !== topic) return false;
  if (status && s.status !== status) return false;
  if (!q) return true;
  const hay = `${s.name} ${s.area || ''} ${s.neighbourhood || ''} ${s.website || ''} ${s.instagram || ''}`.toLowerCase();
  return hay.includes(q);
}

function renderSources() {
  const list = state.src.list || [];
  $('#src-total').textContent = `· ${list.length}`;
  const q = $('#src-search').value.trim().toLowerCase();
  const topic = $('#src-topic').value;
  const status = $('#src-status').value;
  const hits = list.filter(s => srcMatches(s, q, topic, status));
  const wrap = $('#src-list');
  wrap.innerHTML = '';
  for (const s of hits.slice(0, state.src.limit)) wrap.append(srcRow(s));
  $('#src-more').hidden = hits.length <= state.src.limit;
  $('#src-more').textContent = `Mostrar mais (${hits.length - Math.min(state.src.limit, hits.length)} escondidos)`;
  if (!hits.length) wrap.append(el('p', { className: 'note', textContent: 'Nenhum local corresponde à pesquisa.' }));
}

function srcRow(s) {
  const t = (state.taxonomy.topics || []).find(x => x.id === s.topic);
  const bits = [s.neighbourhood || s.area || '—', STATUS_PT[s.status] || s.status];
  const meta = el('p', { className: 'db-meta' });
  meta.append(bits.join(' · ') + ' · ');
  if (s.website) meta.append(el('a', { href: s.website, target: '_blank', rel: 'noopener', textContent: s.website.replace(/^https?:\/\/(www\.)?/, '').slice(0, 40) }));
  if (s.instagram) meta.append(' · ', el('a', { href: s.instagram, target: '_blank', rel: 'noopener', textContent: '@instagram' }));
  if (!s.crawlable) meta.append(' · não recolhível');
  const row = el('div', { className: 'db-row' },
    el('span', { className: 'db-emoji', textContent: t ? t.emoji : '·' }),
    el('div', { className: 'db-main' }, el('p', { className: 'db-title', textContent: s.name }), meta),
    el('div', { className: 'db-actions' },
      el('button', { className: 'btn ghost btn-sm', type: 'button', textContent: 'Editar', onclick: () => openSrcEditor(s) }),
      el('button', { className: 'btn ghost btn-sm', type: 'button', textContent: 'Eliminar', onclick: () => deleteSource(s) })));
  return row;
}

function field(labelText, input) {
  return el('div', { className: 'field' }, el('label', { textContent: labelText }), input);
}

function openSrcEditor(s) {
  const isNew = !s;
  const v = s || { name: '', neighbourhood: '', website: '', instagram: '', topic: 'music', status: 'active', crawlable: true, description: '' };
  const name = el('input', { value: v.name });
  const nb = el('select');
  fillSelect(nb, [['', '—'], ...(state.taxonomy.neighbourhoods || []).map(n => [n.name, n.name])], v.neighbourhood || '');
  const site = el('input', { value: v.website || '', placeholder: 'https://…' });
  const insta = el('input', { value: v.instagram || '', placeholder: 'https://instagram.com/…' });
  const topic = el('select');
  fillSelect(topic, state.taxonomy.topics.map(t => [t.id, t.emoji + ' ' + t.label]), v.topic);
  const status = el('select');
  fillSelect(status, Object.entries(STATUS_PT), v.status);
  const crawl = el('input', { type: 'checkbox', checked: !!v.crawlable });
  const desc = el('input', { value: v.description || '', placeholder: 'uma linha sobre o local' });
  const line = el('span', { className: 'status-line' });

  const save = async () => {
    if (!name.value.trim()) { line.textContent = 'O nome é obrigatório.'; return; }
    line.textContent = 'A guardar…';
    try {
      await mutateSources((list) => {
        const tax = state.taxonomy.topics.find(t => t.id === topic.value);
        const nbObj = (state.taxonomy.neighbourhoods || []).find(n => n.name === nb.value);
        const base = {
          name: name.value.trim(), area: nb.value || '', neighbourhood: nb.value || null,
          zone: nbObj ? nbObj.zone : null, description: desc.value.trim(),
          website: site.value.trim() || null, instagram: insta.value.trim() || null,
          topic: topic.value, categories: tax ? tax.categories.slice(0, 1) : [1],
          primary_category: tax ? tax.categories[0] : 1, status: status.value,
          crawlable: crawl.checked && !!site.value.trim(),
        };
        if (isNew) {
          let id = slugify(base.name); let n = 2;
          while (list.some(x => x.id === id)) id = slugify(base.name) + '-' + n++;
          list.push({ id, other_urls: [], facebook: null, handles: [], provider: base.crawlable ? 'generic' : 'social', flags: ['adicionado via admin'], dead_signals: 0, ...base });
        } else {
          const target = list.find(x => x.id === s.id);
          if (!target) throw new Error('local já não existe no repositório');
          Object.assign(target, base);
        }
      }, isNew ? `sources: adicionar ${name.value.trim()} via admin` : `sources: editar ${name.value.trim()} via admin`);
      closeSrcEditor();
      renderSources();
      $('#src-status-line').textContent = '✓ Guardado no repositório.';
    } catch (e) { line.textContent = 'Erro: ' + e.message; }
  };

  const form = el('div', { className: 'inline-form' },
    el('p', { className: 'panel-sub', style: 'margin:0', textContent: isNew ? 'Novo local' : 'Editar: ' + v.name }),
    el('div', { className: 'form-grid' },
      field('Nome', name), field('Bairro / zona', nb), field('Website', site), field('Instagram', insta),
      field('Tema', topic), field('Estado', status)),
    field('Descrição', desc),
    el('label', { className: 'field-check' }, crawl, 'Incluir na recolha automática (precisa de website)'),
    el('div', { className: 'row-actions' },
      el('button', { className: 'btn primary', type: 'button', textContent: 'Guardar', onclick: save }),
      el('button', { className: 'btn ghost', type: 'button', textContent: 'Cancelar', onclick: closeSrcEditor }),
      line));
  const host = $('#src-editor');
  host.innerHTML = '';
  host.append(form);
  host.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function closeSrcEditor() { $('#src-editor').innerHTML = ''; }

async function deleteSource(s) {
  if (!confirm(`Eliminar «${s.name}» da lista de locais?`)) return;
  $('#src-status-line').textContent = 'A eliminar…';
  try {
    await mutateSources((list) => {
      const i = list.findIndex(x => x.id === s.id);
      if (i < 0) throw new Error('local já não existe');
      list.splice(i, 1);
    }, `sources: eliminar ${s.name} via admin`);
    renderSources();
    $('#src-status-line').textContent = '✓ Eliminado.';
  } catch (e) { $('#src-status-line').textContent = 'Erro: ' + e.message; }
}

/* fetch fresh → mutate → commit (avoids clobbering crawler commits) */
async function mutateSources(mutator, message) {
  if (!hasGh()) throw new Error('configure o GitHub no separador Setup');
  const { text, sha } = await ghGetFile(SOURCES_PATH);
  const payload = JSON.parse(text);
  mutator(payload.sources);
  payload.count = payload.sources.length;
  state.src.sha = await ghPutFile(SOURCES_PATH, JSON.stringify(payload, null, 2) + '\n', sha, message);
  state.src.list = payload.sources;
  $('#src-total').textContent = `· ${payload.sources.length}`;
}

/* ----- eventos da semana ----- */
async function loadWeekEvents() {
  const line = $('#ev-status-line');
  line.textContent = 'A carregar eventos…';
  try {
    const idx = await (await fetch('/data/weeks/index.json', { cache: 'no-cache' })).json();
    const latest = (idx.weeks || []).slice().sort((a, b) => b.start.localeCompare(a.start))[0];
    if (!latest) { line.textContent = 'Ainda não há semanas publicadas.'; return; }
    state.ev.path = 'docs/data/weeks/' + latest.file;
    if (hasGh()) {
      const { text, sha } = await ghGetFile(state.ev.path);
      state.ev.data = JSON.parse(text);
      state.ev.sha = sha;
      line.textContent = state.ev.data.is_sample ? 'Esta é a semana de exemplo — será substituída pela primeira recolha real.' : '';
    } else {
      state.ev.data = await (await fetch('/data/weeks/' + latest.file, { cache: 'no-cache' })).json();
      state.ev.sha = null;
      line.textContent = 'Modo de leitura — configure o GitHub (Setup) para editar.';
    }
    renderEvents();
  } catch (e) { line.textContent = 'Erro a carregar eventos: ' + e.message; }
}

function renderEvents() {
  const evs = state.ev.data?.events || [];
  $('#ev-total').textContent = `· ${evs.length}`;
  const q = $('#ev-search').value.trim().toLowerCase();
  const topic = $('#ev-topic').value;
  const hits = evs.filter(e =>
    (!topic || e.topic === topic) &&
    (!q || `${e.title} ${e.venue} ${e.neighbourhood || ''}`.toLowerCase().includes(q)));
  const wrap = $('#ev-list');
  wrap.innerHTML = '';
  for (const e of hits.slice(0, state.ev.limit)) wrap.append(evRow(e));
  $('#ev-more').hidden = hits.length <= state.ev.limit;
  $('#ev-more').textContent = `Mostrar mais (${hits.length - Math.min(state.ev.limit, hits.length)} escondidos)`;
  if (!hits.length) wrap.append(el('p', { className: 'note', textContent: 'Nenhum evento corresponde à pesquisa.' }));
}

function evRow(e) {
  const d = new Date((e.start || '').slice(0, 10) + 'T00:00:00');
  const day = DAY_PT[e.days?.[0]] || '';
  const time = (e.start || '').slice(11, 16);
  const when = e.ongoing ? 'em curso' : `${day} ${d.getDate()}${time ? ' · ' + time : ''}`;
  const tags = [topicLabel(e.topic), e.price?.is_free ? 'Grátis' : (e.price?.text || '')].filter(Boolean).join(' · ');
  return el('div', { className: 'db-row' },
    el('span', { className: 'db-emoji', textContent: (state.taxonomy.topics.find(t => t.id === e.topic) || {}).emoji || '·' }),
    el('div', { className: 'db-main' },
      el('p', { className: 'db-title', textContent: e.title }),
      el('p', { className: 'db-meta', textContent: `${when} · ${[e.venue, e.neighbourhood].filter(Boolean).join(' · ')} · ${tags}` })),
    el('div', { className: 'db-actions' },
      el('button', { className: 'btn ghost btn-sm', type: 'button', textContent: 'Editar', onclick: () => openEvEditor(e) }),
      el('button', { className: 'btn ghost btn-sm', type: 'button', textContent: 'Eliminar', onclick: () => deleteEvent(e) })));
}

function openEvEditor(e) {
  const isNew = !e;
  const week = state.ev.data;
  const v = e || { title: '', start: week?.week_start || '', venue: '', neighbourhood: '', topic: 'music', price: { is_free: false, text: '' }, language: ['pt'], url: '', description: '' };
  const title = el('input', { value: v.title });
  const date = el('input', { type: 'date', value: (v.start || '').slice(0, 10), min: week?.week_start, max: week?.week_end });
  const time = el('input', { type: 'time', value: (v.start || '').slice(11, 16) });
  const venue = el('input', { value: v.venue || '' });
  const nb = el('select');
  fillSelect(nb, [['', '—'], ...(state.taxonomy.neighbourhoods || []).map(n => [n.name, n.name])], v.neighbourhood || '');
  const topic = el('select');
  fillSelect(topic, state.taxonomy.topics.filter(t => !t.is_aggregator).map(t => [t.id, t.emoji + ' ' + t.label]), v.topic);
  const free = el('input', { type: 'checkbox', checked: !!v.price?.is_free });
  const price = el('input', { value: v.price?.is_free ? '' : (v.price?.text || ''), placeholder: '€10' });
  const en = el('input', { type: 'checkbox', checked: (v.language || []).includes('en') });
  const url = el('input', { value: v.url || '', placeholder: 'https://…' });
  const desc = el('input', { value: v.description || '' });
  const line = el('span', { className: 'status-line' });

  const save = async () => {
    if (!title.value.trim() || !date.value) { line.textContent = 'Título e data são obrigatórios.'; return; }
    line.textContent = 'A guardar…';
    try {
      await mutateWeek((data) => {
        const dt = new Date(date.value + 'T00:00:00');
        const dayCode = DAYS[(dt.getDay() + 6) % 7];
        const nbObj = (state.taxonomy.neighbourhoods || []).find(n => n.name === nb.value);
        const tax = state.taxonomy.topics.find(t => t.id === topic.value);
        const base = {
          title: title.value.trim(), topic: topic.value, categories: tax ? tax.categories.slice(0, 2) : [],
          venue: venue.value.trim() || '—', neighbourhood: nb.value || null, zone: nbObj ? nbObj.zone : null,
          start: date.value + (time.value ? 'T' + time.value : ''), all_day: !time.value,
          ongoing: false, days: [dayCode],
          price: free.checked ? { is_free: true, min: 0, currency: 'EUR', text: 'Grátis' }
            : { is_free: false, min: parseFloat((price.value.match(/[\d.,]+/) || [''])[0]?.replace(',', '.')) || null, currency: 'EUR', text: price.value.trim() },
          language: en.checked ? ['pt', 'en'] : ['pt'],
          url: url.value.trim() || null, description: desc.value.trim(),
        };
        if (isNew) {
          data.events.push({ id: 'man-' + Math.random().toString(36).slice(2, 10), source_id: 'manual', source: 'manual', end: null, image: null, ...base });
        } else {
          const target = data.events.find(x => x.id === e.id);
          if (!target) throw new Error('o evento já não existe no ficheiro');
          Object.assign(target, base);
        }
      }, isNew ? `events: adicionar ${title.value.trim()} via admin` : `events: editar ${title.value.trim()} via admin`);
      closeEvEditor();
      renderEvents();
      $('#ev-status-line').textContent = '✓ Guardado — o site atualiza em ~1 minuto.';
    } catch (err) { line.textContent = 'Erro: ' + err.message; }
  };

  const form = el('div', { className: 'inline-form' },
    el('p', { className: 'panel-sub', style: 'margin:0', textContent: isNew ? 'Novo evento' : 'Editar: ' + v.title }),
    el('div', { className: 'form-grid' },
      field('Título', title), field('Data', date), field('Hora (opcional)', time), field('Local', venue),
      field('Bairro / zona', nb), field('Tema', topic), field('Preço', price), field('Link', url)),
    field('Descrição', desc),
    el('div', { className: 'row-actions' },
      el('label', { className: 'field-check' }, free, 'Grátis'),
      el('label', { className: 'field-check' }, en, 'EN (inglês)')),
    el('div', { className: 'row-actions' },
      el('button', { className: 'btn primary', type: 'button', textContent: 'Guardar', onclick: save }),
      el('button', { className: 'btn ghost', type: 'button', textContent: 'Cancelar', onclick: closeEvEditor }),
      line));
  const host = $('#ev-editor');
  host.innerHTML = '';
  host.append(form);
  host.scrollIntoView({ behavior: 'smooth', block: 'center' });
}
function closeEvEditor() { $('#ev-editor').innerHTML = ''; }

async function deleteEvent(e) {
  if (!confirm(`Eliminar o evento «${e.title}»?`)) return;
  $('#ev-status-line').textContent = 'A eliminar…';
  try {
    await mutateWeek((data) => {
      const i = data.events.findIndex(x => x.id === e.id);
      if (i < 0) throw new Error('o evento já não existe');
      data.events.splice(i, 1);
    }, `events: eliminar ${e.title.slice(0, 50)} via admin`);
    renderEvents();
    $('#ev-status-line').textContent = '✓ Eliminado — o site atualiza em ~1 minuto.';
  } catch (err) { $('#ev-status-line').textContent = 'Erro: ' + err.message; }
}

async function mutateWeek(mutator, message) {
  if (!hasGh()) throw new Error('configure o GitHub no separador Setup');
  const { text, sha } = await ghGetFile(state.ev.path);
  const data = JSON.parse(text);
  mutator(data);
  data.event_count = data.events.length;
  state.ev.sha = await ghPutFile(state.ev.path, JSON.stringify(data, null, 2) + '\n', sha, message);
  state.ev.data = data;
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
  const p = state.proposed;
  (p.closures || []).forEach(x => { state.decisions.closures[x.id] = true; const b = $(`#closures-${x.id}`); if (b) b.checked = true; });
  (p.new_venues || []).forEach((v, i) => { state.decisions.new_venues[String(i)] = true; const b = $(`#new_venues-${i}`); if (b) b.checked = true; });
});
$('#src-search').addEventListener('input', () => { state.src.limit = 50; renderSources(); });
$('#src-topic').addEventListener('change', () => { state.src.limit = 50; renderSources(); });
$('#src-status').addEventListener('change', () => { state.src.limit = 50; renderSources(); });
$('#src-more').addEventListener('click', () => { state.src.limit += 100; renderSources(); });
$('#src-add').addEventListener('click', () => openSrcEditor(null));
$('#ev-search').addEventListener('input', () => { state.ev.limit = 50; renderEvents(); });
$('#ev-topic').addEventListener('change', () => { state.ev.limit = 50; renderEvents(); });
$('#ev-more').addEventListener('click', () => { state.ev.limit += 100; renderEvents(); });
$('#ev-add').addEventListener('click', () => openEvEditor(null));

initTabs();
loadConfig();
loadStatus();
loadProposed();
refreshSecretState();
loadLimits();

/* ---------- ticker ---------- */
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
