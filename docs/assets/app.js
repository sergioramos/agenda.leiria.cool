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
  filters: { q: '', topics: new Set(), days: new Set(), free: false },
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
  const track = document.querySelector('.ticker-track');
  if (typeof buildTicker === 'function') buildTicker(track, tickerSegment);
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
  if (f.days.size && !(ev.days || []).some(d => f.days.has(d))) return false;
  if (f.q) {
    // build the search haystack once per event and cache it — rebuilding it for
    // every event on every keystroke was the cause of the search lag
    if (ev._hay === undefined) {
      const cats = (ev.categories || []).map(c => state.taxonomy.categories[c] || '').join(' ');
      const lineup = (ev.lineup || []).join(' ');
      ev._hay = `${ev.title} ${ev.venue} ${ev.neighbourhood || ''} ${ev.description || ''} ${cats} ${lineup}`.toLowerCase();
    }
    if (!ev._hay.includes(f.q)) return false;
  }
  return true;
}

/* ---------- rendering ---------- */
function renderTopicChips() {
  const wrap = $('#topic-chips');
  // swap skeleton pills → real chips in place (same height, container already
  // revealed by the page wave — no separate fade, so the reveal isn't fought)
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

/* ---------- skeleton (same DOM as a real card, shimmer in each slot) ---------- */
function skBar(css) { return el('span', { className: 'sk-bar', style: css }); }

function skeletonCard() {
  const media = el('div', { className: 'card-media' }, el('div', { className: 'card-img sk-bar' }));
  const text = el('div', { className: 'card-text' },
    skBar('width:62%;height:18px'), skBar('width:42%;height:14px'));
  const save = skBar('width:118px;height:34px;flex:none');
  const badges = el('div', { className: 'badges' }, skBar('width:96px;height:24px'), skBar('width:48px;height:24px'));
  const body = el('div', { className: 'body' },
    el('div', { className: 'card-toprow' }, text, save), badges);
  const when = el('div', { className: 'when' },
    skBar('width:26px;height:12px'), skBar('width:30px;height:30px'), skBar('width:24px;height:12px'));
  return el('article', { className: 'card is-skeleton', ariaHidden: 'true' },
    media, body, el('span', { className: 'card-divider', ariaHidden: 'true' }), when);
}

function showSkeleton(n = 5) {
  const results = $('#results');
  const head = el('div', { className: 'sk-head' }, skBar('width:84px;height:12px'), skBar('width:220px;height:24px'));
  const cards = el('div', { className: 'cards' });
  for (let i = 0; i < n; i++) cards.append(skeletonCard());
  const wrap = el('div', { className: 'skeleton' }, head, cards);
  results.innerHTML = '';
  results.append(wrap);  // shown inside #results, which the page wave reveals
}

/* one coordinated reveal: walk every .rv block in document order (top → bottom),
   give each a growing delay, then flip body.revealed so they rise in as one wave */
function revealPage() {
  if (document.body.classList.contains('revealed')) return;
  const blocks = [...document.querySelectorAll('.rv')];
  blocks.forEach((elm, i) => elm.style.setProperty('--d', Math.min(i, 13) * 55 + 'ms'));
  document.body.classList.add('revealed');
  // safety: once the wave should be done, pin the end state so content is never
  // left hidden if the browser paused the transition (e.g. a background tab)
  setTimeout(() => blocks.forEach(e => {
    e.style.transition = 'none'; e.style.opacity = '1';
  }), 1600);
}

/* lucide icons (inline so they tint via currentColor) */
const PIN_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0"/><circle cx="12" cy="10" r="3"/></svg>';
const CAL_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16 19h6"/><path d="M16 2v4"/><path d="M19 16v6"/><path d="M21 12.598V6a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8.5"/><path d="M3 10h18"/><path d="M8 2v4"/></svg>';

function mapsUrl(ev) {
  // for a generic/unnamed venue (city-wide events, "TBA", "Secret Location"),
  // exact coordinates beat a name search; otherwise the name shows the place card
  const generic = !ev.venue || /^(lisboa|tba|secret|local)/i.test(ev.venue);
  if (generic && ev.lat && ev.lng) {
    return 'https://www.google.com/maps/search/?api=1&query=' + ev.lat + ',' + ev.lng;
  }
  const q = [ev.venue, ev.neighbourhood, 'Lisboa'].filter(Boolean).join(', ');
  return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(q);
}

function gcalUrl(ev) {
  const pad = n => String(n).padStart(2, '0');
  let dates;
  if (!ev.all_day && (ev.start || '').length > 10) {
    const fmt = d => `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`;
    const s = new Date(ev.start);
    const e = new Date(s.getTime() + 2 * 3600e3); // no published end time — assume 2h
    dates = `${fmt(s)}/${fmt(e)}`;
  } else {
    // all-day (Google wants an EXCLUSIVE end date)
    const end = new Date((ev.end || ev.start).slice(0, 10) + 'T00:00:00');
    end.setDate(end.getDate() + 1);
    dates = `${ev.start.slice(0, 10).replace(/-/g, '')}/` +
      `${end.getFullYear()}${pad(end.getMonth() + 1)}${pad(end.getDate())}`;
  }
  const p = new URLSearchParams({
    action: 'TEMPLATE', text: ev.title, dates,
    details: ev.url || '', location: [ev.venue, ev.neighbourhood, 'Lisboa'].filter(Boolean).join(', '),
    ctz: 'Europe/Lisbon',
  });
  return 'https://calendar.google.com/calendar/render?' + p.toString();
}

function card(ev) {
  const d = eventDate(ev);
  const topic = state.topicById[ev.topic];

  const when = el('div', { className: 'when' });
  if (ev.ongoing) {
    // a run/exhibition: point at its closing day ("até 12 · em curso"). Show the
    // month when the end falls outside the week being viewed, so "até 12" can't
    // be misread as this month when it's really 12 Jul.
    const endD = ev.end ? new Date(ev.end.slice(0, 10) + 'T00:00:00') : null;
    const ref = new Date(state.week.week_start + 'T00:00:00');
    const dateSpan = el('span', { className: 'date', textContent: (endD || d).getDate() || '' });
    if (endD && (endD.getMonth() !== ref.getMonth() || endD.getFullYear() !== ref.getFullYear())) {
      dateSpan.append(el('span', { className: 'date-mon', textContent: ' ' + MONTHS[endD.getMonth()] }));
    }
    when.append(
      el('span', { className: 'day', textContent: endD ? 'até' : (DAY_LABEL[ev.days?.[0]] || 'sem') }),
      dateSpan,
      el('span', { className: 'ongoing', textContent: 'em curso' }));
  } else {
    // always show the time line; an em-dash marks "no time captured"
    const time = (ev.start || '').slice(11, 16);
    when.append(
      el('span', { className: 'day', textContent: DAY_LABEL[ev.days?.[0]] || '' }),
      el('span', { className: 'date', textContent: d.getDate() || '' }),
      el('span', { className: 'time', textContent: time || '—' }));
  }

  // square artwork; topic emoji stands in until a real poster is found.
  const media = el('div', { className: 'card-img', ariaHidden: 'true' });
  const placeholder = () => { media.textContent = ''; media.classList.add('ph'); media.textContent = topic?.emoji || '📌'; };
  if (ev.image) {
    const img = el('img', { src: ev.image, alt: '', loading: 'lazy' });
    img.onerror = () => { img.remove(); placeholder(); };
    // a broken/blank/tiny image (logo pixel, icon) → fall back to the emoji
    img.onload = () => { if (img.naturalWidth < 64 || img.naturalHeight < 64) { img.remove(); placeholder(); } };
    media.append(img);
  } else placeholder();
  const mediaWrap = el('div', { className: 'card-media' }, media);

  // title is underlined only when it links somewhere — an honest link affordance
  const h = el('h3', {}, ev.url
    ? el('a', { href: ev.url, target: '_blank', rel: 'noopener', textContent: ev.title })
    : el('span', { className: 'no-link', textContent: ev.title }));
  // the pin lives inside the link so it highlights (and clicks) with the text
  const venueLine = el('a', {
    className: 'venue-line', href: mapsUrl(ev), target: '_blank', rel: 'noopener',
    title: 'Abrir no Google Maps',
  });
  venueLine.insertAdjacentHTML('afterbegin', PIN_SVG);
  venueLine.append(el('span', {
    textContent: [ev.venue, ev.neighbourhood].filter(Boolean).join(' · '),
  }));

  const save = el('a', {
    className: 'save-date', href: gcalUrl(ev), target: '_blank', rel: 'noopener',
    title: 'Adicionar ao Google Calendar',
  });
  save.insertAdjacentHTML('afterbegin', CAL_SVG);
  save.append(el('span', { textContent: 'Guardar data' }));

  const badges = el('div', { className: 'badges' });
  if (topic) badges.append(el('span', { className: 'badge', textContent: topic.label }));
  if (ev.price?.is_free) badges.append(el('span', { className: 'badge free', textContent: 'Grátis' }));
  else if (ev.price?.text) badges.append(el('span', { className: 'badge', textContent: ev.price.text }));
  if ((ev.language || []).includes('en')) badges.append(el('span', { className: 'badge', textContent: 'EN' }));

  const body = el('div', { className: 'body' },
    el('div', { className: 'card-toprow' },
      el('div', { className: 'card-text' }, h, venueLine),
      save),
    badges);

  return el('article', { className: 'card' },
    mediaWrap, body,
    el('span', { className: 'card-divider', ariaHidden: 'true' }),
    when);
}

/* event cards fade in one by one on load (a gentle top-to-bottom cascade), so
   the list builds card-by-card instead of appearing all at once */
function revealCards() {
  const cards = [...document.querySelectorAll('.card-rv')];
  const STEP = 110, CAP = 10; // first ~10 (the visible ones) clearly one-by-one; rest follow
  cards.forEach((c, i) => setTimeout(() => c.classList.add('in'), Math.min(i, CAP) * STEP));
  // safety: pin everything visible after the cascade, even if a transition was
  // paused (e.g. background tab) — content is never left hidden
  setTimeout(() => cards.forEach(c => { c.style.transition = 'none'; c.style.opacity = '1'; c.style.transform = 'none'; }),
    CAP * STEP + 700);
}

function render(animate = false) {
  const results = $('#results');
  const visible = state.week.events.filter(matches);
  $('#result-count').textContent = `${visible.length} evento${visible.length === 1 ? '' : 's'}`;
  $('#empty').hidden = visible.length !== 0;

  const order = state.taxonomy.topics.map(t => t.id);
  const groups = {};
  for (const ev of visible) (groups[ev.topic] ||= []).push(ev);

  const inner = el('div', { className: 'results-inner' });
  for (const tid of order) {
    const list = groups[tid];
    if (!list || !list.length) continue;
    const t = state.topicById[tid];
    list.sort((a, b) => (a.start || '').localeCompare(b.start || ''));
    const sec = el('section', { className: 'topic-section' });
    const h2 = el('h2', {});
    t.label.split(' & ').forEach((part, i) => {
      if (i) h2.append(el('span', { className: 'amp', textContent: ' & ' }));
      h2.append(document.createTextNode(part));
    });
    sec.append(el('div', { className: 'topic-head' },
      el('div', { className: 'topic-head-text' },
        el('span', { className: 'topic-kicker', textContent: `${list.length} ${list.length === 1 ? 'evento' : 'eventos'}` }),
        h2)));
    const cards = el('div', { className: 'cards' });
    list.forEach(ev => {
      const c = card(ev);
      if (animate) c.classList.add('card-rv'); // hidden until revealed one by one
      cards.append(c);
    });
    sec.append(cards);
    inner.append(sec);
  }
  results.innerHTML = '';
  results.append(inner);
  if (animate) revealCards(); // cards fade in one by one as they enter view
  renderActiveFilterNote();
  refreshChipStates();
  syncHash();
}

function renderActiveFilterNote() {
  const f = state.filters;
  const bits = [];
  if (f.topics.size) bits.push(`${f.topics.size} tema${f.topics.size > 1 ? 's' : ''}`);
  if (f.days.size) bits.push(`${f.days.size} dia${f.days.size > 1 ? 's' : ''}`);
  if (f.free) bits.push('só grátis');
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
  $$('#price-chips .chip').forEach(b => b.setAttribute('aria-pressed', (b.dataset.price === 'free') === state.filters.free));
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
  if (f.free) p.set('free', '1');
  const s = p.toString();
  history.replaceState(null, '', s ? '#' + s : location.pathname);
}
function loadHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const f = state.filters;
  f.q = (p.get('q') || '').toLowerCase();
  f.topics = new Set((p.get('t') || '').split(',').filter(Boolean));
  f.days = new Set((p.get('d') || '').split(',').filter(Boolean));
  f.free = p.get('free') === '1';
  if (f.q) $('#search').value = f.q;
}

/* An image reused by events with different titles is a venue logo / default
   banner, not a poster — drop it so the topic emoji shows instead. */
function dropSharedImages(events) {
  const titlesByImg = {};
  for (const e of events) {
    if (!e.image) continue;
    (titlesByImg[e.image] ||= new Set()).add((e.title || '').toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 30));
  }
  for (const e of events) if (e.image && titlesByImg[e.image].size >= 2) e.image = null;
}

/* ---------- week loading ---------- */
async function loadWeek(fileEntry) {
  state.week = await getJSON('./data/weeks/' + fileEntry.file);
  dropSharedImages(state.week.events);
  const gen = new Date(state.week.generated_at);
  const wv = $('#week-value');
  if (wv) wv.textContent = fmtRange(state.week.week_start, state.week.week_end);
  state.ticker = state.week.is_sample
    ? { text: '⚠ Dados de exemplo', accent: true }
    : { text: 'Atualizado ' + gen.toLocaleDateString('pt-PT'), accent: false };
  buildTopbar();
  const stats = $('#footer-stats');
  stats.textContent =
    `${state.week.event_count} eventos · ${state.week.source_count} fontes · atualizado a ${gen.toLocaleDateString('pt-PT')}`;
  // chips depend on the loaded week (dates, present topics)
  renderTopicChips();
  renderDayChips();
  // fade the skeleton out, then the real sections rise in ("fade into" the cards)
  const sk = $('#results .skeleton');
  if (sk) { sk.style.opacity = '0'; setTimeout(() => render(true), 380); }
  else render(true);
}

async function init() {
  setTimeout(() => document.body.classList.add('revealed'), 2500); // safety: never stay hidden
  showSkeleton();  // visible structure while the data loads
  buildTopbar();   // base ticker text present BEFORE the reveal, so it fades in with the bar
  revealPage();    // reveal the whole page (with skeleton) top-to-bottom, at once
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
  let searchT;
  $('#search').addEventListener('input', e => {
    state.filters.q = e.target.value.trim().toLowerCase();
    clearTimeout(searchT);
    searchT = setTimeout(render, 120);   // debounce: re-render after the user pauses
  });
  const filtToggle = $('#filters-toggle');
  filtToggle.onclick = () => {
    const collapse = $('#filter-collapse');
    const open = !collapse.classList.contains('open');
    collapse.classList.toggle('open', open);
    filtToggle.setAttribute('aria-expanded', open);
    const sign = $('#filt-sign'); if (sign) sign.textContent = open ? '−' : '+';
  };
  $$('#price-chips .chip').forEach(b => b.onclick = () => { state.filters.free = b.dataset.price === 'free'; render(); });
  $('#empty-clear').onclick = () => {
    state.filters = { q: '', topics: new Set(), days: new Set(), free: false };
    $('#search').value = '';
    renderTopicChips(); render();
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
