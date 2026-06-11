/* Lisbon Events — client-side app.
   Reads ./taxonomy.json + ./data/weeks/index.json + a week file, renders and filters. */
'use strict';

const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const DAY_LABEL = { mon: 'Seg', tue: 'Ter', wed: 'Qua', thu: 'Qui', fri: 'Sex', sat: 'Sáb', sun: 'Dom' };
const MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

const state = {
  taxonomy: null,
  topicById: {},
  week: null,
  ticker: { text: '', accent: false },
  filters: { q: '', topics: new Set(), days: new Set(), neighbourhoods: new Set(), free: false, zone: 'all' },
};

/* ---------- announcement-bar ticker (Figma 19:2073 — 1:1) ---------- */
const TICKER_BASE = 'Pregoeiro · O pregão semanal de Lisboa';

function tickerSegment() {
  const seg = el('span', { className: 'ticker-item' });
  seg.append(document.createTextNode(TICKER_BASE + (state.ticker.text ? ' · ' : '')));
  if (state.ticker.text) {
    seg.append(el('span', {
      className: 'ticker-status' + (state.ticker.accent ? ' accent' : ''),
      textContent: state.ticker.text,
    }));
  }
  return seg;
}

function buildTopbar() {
  if (typeof buildTicker === 'function') buildTicker(document.querySelector('.ticker-track'), tickerSegment);
}

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (tag, props = {}, ...kids) => {
  const n = Object.assign(document.createElement(tag), props);
  for (const k of kids) n.append(k);
  return n;
};

async function getJSON(path) {
  const res = await fetch(path, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function fmtRange(startISO, endISO) {
  const s = new Date(startISO + 'T00:00:00'), e = new Date(endISO + 'T00:00:00');
  const sameMonth = s.getMonth() === e.getMonth();
  return sameMonth
    ? `${s.getDate()}–${e.getDate()} ${MONTHS[e.getMonth()]} ${e.getFullYear()}`
    : `${s.getDate()} ${MONTHS[s.getMonth()]} – ${e.getDate()} ${MONTHS[e.getMonth()]} ${e.getFullYear()}`;
}

function eventDate(ev) {
  return new Date((ev.start || '').slice(0, 10) + 'T00:00:00');
}

/* ---------- filtering ---------- */
function matches(ev) {
  const f = state.filters;
  if (f.topics.size && !f.topics.has(ev.topic)) return false;
  if (f.free && !(ev.price && ev.price.is_free)) return false;
  if (f.zone !== 'all' && ev.zone !== f.zone) return false;
  if (f.neighbourhoods.size && !f.neighbourhoods.has(ev.neighbourhood)) return false;
  if (f.days.size && !(ev.days || []).some(d => f.days.has(d))) return false;
  if (f.q) {
    const cats = (ev.categories || []).map(c => state.taxonomy.categories[c] || '').join(' ');
    const hay = `${ev.title} ${ev.venue} ${ev.neighbourhood || ''} ${ev.description || ''} ${cats}`.toLowerCase();
    if (!hay.includes(f.q)) return false;
  }
  return true;
}

/* ---------- rendering ---------- */
function renderTopicChips() {
  const wrap = $('#topic-chips');
  wrap.innerHTML = '';
  const counts = {};
  for (const ev of state.week.events) counts[ev.topic] = (counts[ev.topic] || 0) + 1;
  for (const t of state.taxonomy.topics) {
    if (t.is_aggregator) continue;
    const n = counts[t.id] || 0;
    if (!n) continue;
    const chip = el('button', { className: 'chip', type: 'button' },
      el('span', { className: 'emoji', textContent: t.emoji }),
      el('span', { textContent: t.label }),
      el('span', { className: 'count', textContent: n }));
    chip.setAttribute('aria-pressed', state.filters.topics.has(t.id));
    chip.onclick = () => { toggleSet(state.filters.topics, t.id); apply(); };
    wrap.append(chip);
  }
}

function renderDayChips() {
  const wrap = $('#day-chips');
  wrap.innerHTML = '';
  const ws = new Date(state.week.week_start + 'T00:00:00');
  DAYS.forEach((d, i) => {
    const date = new Date(ws); date.setDate(ws.getDate() + i);
    const chip = el('button', { className: 'chip', type: 'button' },
      el('span', { textContent: `${DAY_LABEL[d]} ${date.getDate()}` }));
    chip.setAttribute('aria-pressed', state.filters.days.has(d));
    chip.onclick = () => { toggleSet(state.filters.days, d); apply(); };
    wrap.append(chip);
  });
}

function renderNeighbourhoodChips() {
  const wrap = $('#neighbourhood-chips');
  wrap.innerHTML = '';
  const present = new Set(state.week.events.map(e => e.neighbourhood).filter(Boolean));
  const f = state.filters;
  for (const nb of state.taxonomy.neighbourhoods) {
    if (!present.has(nb.name)) continue;
    if (f.zone !== 'all' && nb.zone !== f.zone) continue;
    const chip = el('button', { className: 'chip', type: 'button' }, el('span', { textContent: nb.name }));
    chip.setAttribute('aria-pressed', f.neighbourhoods.has(nb.name));
    chip.onclick = () => { toggleSet(f.neighbourhoods, nb.name); apply(); };
    wrap.append(chip);
  }
}

function card(ev) {
  const d = eventDate(ev);
  const when = el('div', { className: 'when' });
  if (ev.ongoing) {
    // a run/exhibition: point at its closing day when known ("até 14 · em curso")
    const endD = ev.end ? new Date(ev.end.slice(0, 10) + 'T00:00:00') : null;
    when.append(
      el('span', { className: 'day', textContent: endD ? 'até' : (DAY_LABEL[ev.days?.[0]] || 'sem') }),
      el('span', { className: 'date', textContent: (endD || d).getDate() || '' }),
      el('span', { className: 'ongoing', textContent: 'em curso' }));
  } else {
    const time = (ev.start || '').slice(11, 16);
    when.append(
      el('span', { className: 'day', textContent: DAY_LABEL[ev.days?.[0]] || '' }),
      el('span', { className: 'date', textContent: d.getDate() || '' }));
    if (time) when.append(el('span', { className: 'time', textContent: time }));
  }
  const titleEl = ev.url
    ? el('a', { href: ev.url, target: '_blank', rel: 'noopener', textContent: ev.title })
    : document.createTextNode(ev.title);
  const badges = el('div', { className: 'badges' });
  const topic = state.topicById[ev.topic];
  if (topic) badges.append(el('span', { className: 'badge topic', textContent: topic.label }));
  if (ev.price?.is_free) badges.append(el('span', { className: 'badge free', textContent: 'Grátis' }));
  else if (ev.price?.text) badges.append(el('span', { className: 'badge', textContent: ev.price.text }));
  if ((ev.language || []).includes('en')) badges.append(el('span', { className: 'badge', textContent: 'EN' }));

  return el('article', { className: 'card' },
    when,
    el('span', { className: 'card-divider', ariaHidden: 'true' }),
    el('div', { className: 'body' },
      el('div', { className: 'card-text' },
        el('h3', {}, titleEl),
        el('p', { className: 'meta-line', textContent: [ev.venue, ev.neighbourhood].filter(Boolean).join(' · ') })),
      badges));
}

function render() {
  const results = $('#results');
  results.innerHTML = '';
  const visible = state.week.events.filter(matches);
  $('#result-count').textContent = `${visible.length} evento${visible.length === 1 ? '' : 's'}`;
  $('#empty').hidden = visible.length !== 0;

  const order = state.taxonomy.topics.map(t => t.id);
  const groups = {};
  for (const ev of visible) (groups[ev.topic] ||= []).push(ev);

  for (const tid of order) {
    const list = groups[tid];
    if (!list || !list.length) continue;
    const t = state.topicById[tid];
    list.sort((a, b) => (a.start || '').localeCompare(b.start || ''));
    const sec = el('section', { className: 'topic-section' });
    sec.append(el('div', { className: 'topic-head' },
      el('span', { className: 'topic-icon', textContent: t.emoji }),
      el('div', { className: 'topic-head-text' },
        el('span', { className: 'topic-kicker', textContent: `${list.length} ${list.length === 1 ? 'evento' : 'eventos'}` }),
        el('h2', { textContent: t.label }))));
    const body = el('div', { className: 'topic-body' });
    body.append(el('hr', { className: 'rule-dashed' }));
    const cards = el('div', { className: 'cards' });
    list.forEach(ev => cards.append(card(ev)));
    body.append(cards);
    sec.append(body);
    results.append(sec);
  }
  renderActiveFilterNote();
  refreshChipStates();
  syncHash();
}

function renderActiveFilterNote() {
  const f = state.filters;
  const bits = [];
  if (f.topics.size) bits.push(`${f.topics.size} tema${f.topics.size > 1 ? 's' : ''}`);
  if (f.days.size) bits.push(`${f.days.size} dia${f.days.size > 1 ? 's' : ''}`);
  if (f.neighbourhoods.size) bits.push(`${f.neighbourhoods.size} zona${f.neighbourhoods.size > 1 ? 's' : ''}`);
  if (f.free) bits.push('só grátis');
  if (f.zone !== 'all') bits.push(f.zone === 'city' ? 'cidade de Lisboa' : 'Grande Lisboa');
  if (f.q) bits.push(`“${f.q}”`);
  $('#active-filters').textContent = bits.length ? `· filtrado por ${bits.join(', ')}` : '';
}

function refreshChipStates() {
  $$('#topic-chips .chip').forEach((c, i) => {});
  // re-stamp aria-pressed from state (chips rebuilt only on week load)
  $$('#topic-chips .chip').forEach(c => {
    const label = c.querySelector('span:nth-child(2)')?.textContent;
    const t = state.taxonomy.topics.find(x => x.label === label);
    if (t) c.setAttribute('aria-pressed', state.filters.topics.has(t.id));
  });
  $$('#day-chips .chip').forEach((c, i) => c.setAttribute('aria-pressed', state.filters.days.has(DAYS[i])));
  $$('#neighbourhood-chips .chip').forEach(c => {
    const name = c.textContent;
    c.setAttribute('aria-pressed', state.filters.neighbourhoods.has(name));
  });
  $$('#price-seg button').forEach(b => b.classList.toggle('active', (b.dataset.price === 'free') === state.filters.free));
  $$('#zone-seg button').forEach(b => b.classList.toggle('active', b.dataset.zone === state.filters.zone));
}

function toggleSet(set, v) { set.has(v) ? set.delete(v) : set.add(v); }

function apply() { render(); }

/* ---------- hash state (shareable filters) ---------- */
function syncHash() {
  const f = state.filters;
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.topics.size) p.set('t', [...f.topics].join(','));
  if (f.days.size) p.set('d', [...f.days].join(','));
  if (f.neighbourhoods.size) p.set('n', [...f.neighbourhoods].join(','));
  if (f.free) p.set('free', '1');
  if (f.zone !== 'all') p.set('z', f.zone);
  const s = p.toString();
  history.replaceState(null, '', s ? '#' + s : location.pathname);
}
function loadHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const f = state.filters;
  f.q = (p.get('q') || '').toLowerCase();
  f.topics = new Set((p.get('t') || '').split(',').filter(Boolean));
  f.days = new Set((p.get('d') || '').split(',').filter(Boolean));
  f.neighbourhoods = new Set((p.get('n') || '').split(',').filter(Boolean));
  f.free = p.get('free') === '1';
  f.zone = p.get('z') || 'all';
  if (f.q) $('#search').value = f.q;
}

/* ---------- week loading ---------- */
async function loadWeek(fileEntry) {
  state.week = await getJSON('./data/weeks/' + fileEntry.file);
  const gen = new Date(state.week.generated_at);
  const wv = $('#week-value');
  if (wv) wv.textContent = fmtRange(state.week.week_start, state.week.week_end);
  state.ticker = state.week.is_sample
    ? { text: '⚠ Dados de exemplo', accent: true }
    : { text: 'Atualizado ' + gen.toLocaleDateString('pt-PT'), accent: false };
  buildTopbar();
  $('#footer-stats').textContent =
    `${state.week.event_count} eventos · ${state.week.source_count} fontes · atualizado a ${gen.toLocaleDateString('pt-PT')}`;
  // chips depend on the loaded week (dates, present topics/neighbourhoods)
  renderTopicChips();
  renderDayChips();
  renderNeighbourhoodChips();
  render();
}

async function init() {
  state.taxonomy = await getJSON('./taxonomy.json');
  state.topicById = Object.fromEntries(state.taxonomy.topics.map(t => [t.id, t]));
  loadHash();

  const index = await getJSON('./data/weeks/index.json');
  const weeks = index.weeks.sort((a, b) => b.start.localeCompare(a.start));
  const sel = $('#week-select');
  weeks.forEach((w, i) => sel.append(el('option', {
    value: i, textContent: fmtRange(w.start, w.end) + (w.is_sample ? ' (exemplo)' : '') + (i === 0 ? ' — esta semana' : '')
  })));
  sel.onchange = () => loadWeek(weeks[+sel.value]);

  // wire controls
  $('#search').addEventListener('input', e => { state.filters.q = e.target.value.trim().toLowerCase(); render(); });
  $('#filters-toggle').onclick = e => {
    const panel = $('#filter-panel'); const open = panel.hidden;
    panel.hidden = !open; e.target.setAttribute('aria-expanded', open);
  };
  $$('#price-seg button').forEach(b => b.onclick = () => { state.filters.free = b.dataset.price === 'free'; render(); });
  $$('#zone-seg button').forEach(b => b.onclick = () => {
    state.filters.zone = b.dataset.zone;
    // drop neighbourhood selections outside the chosen zone
    if (state.filters.zone !== 'all') {
      const allowed = new Set(state.taxonomy.neighbourhoods.filter(n => n.zone === state.filters.zone).map(n => n.name));
      state.filters.neighbourhoods = new Set([...state.filters.neighbourhoods].filter(n => allowed.has(n)));
    }
    renderNeighbourhoodChips(); render();
  });
  $('#clear-filters').onclick = $('#empty-clear').onclick = () => {
    state.filters = { q: '', topics: new Set(), days: new Set(), neighbourhoods: new Set(), free: false, zone: 'all' };
    $('#search').value = '';
    renderTopicChips(); renderNeighbourhoodChips(); render();
  };

  await loadWeek(weeks[0]);

  // rebuild the marquee once the real fonts arrive (widths change) and on resize
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(buildTopbar);
  let tickerTimer;
  window.addEventListener('resize', () => { clearTimeout(tickerTimer); tickerTimer = setTimeout(buildTopbar, 200); });
}

init().catch(err => {
  document.querySelector('#results').innerHTML =
    `<p class="empty">Não foi possível carregar os eventos (${err.message}). Se abriu este ficheiro diretamente, use antes o endereço web publicado.</p>`;
  console.error(err);
});
