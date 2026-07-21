import { useDeferredValue, useEffect, useMemo, useState } from 'react';
import type { DayCode, EventItem, Filters, Mock, Taxonomy, Topic, Week } from './types';
import type { Day } from 'date-fns';
import { addDays, format, isSameMonth, parseISO } from 'date-fns';
import { enUS, pt } from 'date-fns/locale';
import { getJSON } from './lib/json';
import { useHashFilters } from './hooks/use-hash-filters';
import Masthead from './components/masthead';
import Chip from './components/chip';
import TopicSection from './components/topic-section';
import { ResultsSkeleton, ChipSkeleton } from './components/skeletons';
import { SearchIcon } from './components/icons';

// Monday-first day-filter keys (enUS abbreviations, lowercased) and their pt
// labels — both from date-fns (day index is Sunday=0, so shift Monday-first by one).
const DAYS = Array.from(
  { length: 7 },
  (_, i) => enUS.localize.day(((i + 1) % 7) as Day, { width: 'abbreviated' }).toLowerCase() as DayCode,
);

const DAY_LABELS = DAYS.map((_, i) => pt.localize.day(((i + 1) % 7) as Day, { width: 'abbreviated' }));

// An image reused by events with different titles is a venue logo / default
// banner, not a poster — drop it so the topic emoji shows instead.
function dropSharedImages(events: EventItem[]): void {
  const titlesByImg: Record<string, Set<string>> = {};
  for (const e of events) {
    if (!e.image) continue;
    (titlesByImg[e.image] ||= new Set()).add(
      (e.title || '').toLowerCase().replace(/[^a-z0-9]+/g, '').slice(0, 30),
    );
  }
  for (const e of events) if (e.image && (titlesByImg[e.image]?.size ?? 0) >= 2) e.image = null;
}

// Search haystack, built once per week (the original mutated ev._hay lazily on
// every keystroke — precomputing avoids that).
function buildHaystacks(events: EventItem[], taxonomy: Taxonomy): Map<string, string> {
  const map = new Map<string, string>();
  for (const ev of events) {
    const cats = (ev.categories || []).map((c) => taxonomy.categories[String(c)] || '').join(' ');
    const lineup = (ev.lineup || []).join(' ');
    map.set(
      ev.id,
      `${ev.title} ${ev.venue} ${ev.neighbourhood || ''} ${ev.description || ''} ${cats} ${lineup}`.toLowerCase(),
    );
  }
  return map;
}

function matches(ev: EventItem, f: Filters, haystacks: Map<string, string>): boolean {
  if (f.topics.size && !f.topics.has(ev.topic)) return false;
  if (f.free && !(ev.price && ev.price.is_free)) return false;
  if (f.hideOngoing && ev.ongoing) return false;
  if (f.days.size && !(ev.days || []).some((d) => f.days.has(d))) return false;
  if (f.q) {
    const h = haystacks.get(ev.id) || '';
    if (!h.includes(f.q)) return false;
  }
  return true;
}

const EMPTY_FILTERS = (): Filters => ({
  q: '',
  topics: new Set(),
  days: new Set(),
  free: false,
  hideOngoing: false,
});

export default function App() {
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null);
  const [week, setWeek] = useState<Week | null>(null);
  const [haystacks, setHaystacks] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState<string | null>(null);

  const { filters, setFilters } = useHashFilters();
  const [query, setQuery] = useState(filters.q);
  const deferredFilters = useDeferredValue(filters);

  const topicById = useMemo<Record<string, Topic>>(
    () => Object.fromEntries((taxonomy?.topics || []).map((t) => [t.id, t])),
    [taxonomy],
  );

  // initial load: one static mock file (taxonomy + a single week of events)
  useEffect(() => {
    getJSON<Mock>('./mock.json')
      .then(({ taxonomy: tax, week: w }) => {
        setTaxonomy(tax);
        dropSharedImages(w.events);
        setHaystacks(buildHaystacks(w.events, tax));
        setTicker(
          w.is_sample
            ? { text: '⚠ Dados de exemplo', accent: true }
            : { text: 'Atualizado ' + format(parseISO(w.generated_at), 'dd/MM/yyyy'), accent: false },
        );
        setWeek(w);
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  // one coordinated top-to-bottom reveal wave (ported from revealPage)
  useEffect(() => {
    const safety = setTimeout(() => document.body.classList.add('revealed'), 2500);
    const blocks = [...document.querySelectorAll<HTMLElement>('.rv')];
    blocks.forEach((el, i) => el.style.setProperty('--d', Math.min(i, 13) * 55 + 'ms'));
    document.body.classList.add('revealed');
    const pin = setTimeout(() => {
      blocks.forEach((e) => {
        e.style.transition = 'none';
        e.style.opacity = '1';
      });
    }, 1600);
    return () => {
      clearTimeout(safety);
      clearTimeout(pin);
    };
  }, []);

  function toggleTopic(id: string) {
    setFilters((f) => {
      const topics = new Set(f.topics);
      topics.has(id) ? topics.delete(id) : topics.add(id);
      return { ...f, topics };
    });
  }
  function toggleDay(d: (typeof DAYS)[number]) {
    setFilters((f) => {
      const days = new Set(f.days);
      days.has(d) ? days.delete(d) : days.add(d);
      return { ...f, days };
    });
  }
  function clearAll() {
    setQuery('');
    setFilters(EMPTY_FILTERS());
  }

  const visible = useMemo(
    () => (week ? week.events.filter((ev) => matches(ev, deferredFilters, haystacks)) : []),
    [week, deferredFilters, haystacks],
  );

  let weekLabel: string | null = null;
  if (week) {
    const s = parseISO(week.week_start);
    const e = parseISO(week.week_end);
    weekLabel = isSameMonth(s, e)
      ? `${format(s, 'd')}–${format(e, 'd MMM yyyy', { locale: pt })}`
      : `${format(s, 'd MMM', { locale: pt })} – ${format(e, 'd MMM yyyy', { locale: pt })}`;
  }

  if (error) {
    return (
      <>
        <main className="wrap" id="main">
          <p className="empty">
            Não foi possível carregar os eventos ({error}). Se abriu este ficheiro diretamente, use antes o
            endereço web publicado.
          </p>
        </main>
      </>
    );
  }

  return (
    <>
      <header className="site-header">
        <div className="wrap">
          <Masthead rv>
            <div className="week-control">
              <span className="week-control-label">Semana</span>
              <span className="week-control-value">
                {week ? (
                  weekLabel
                ) : (
                  <span className="sk-bar" style={{ display: 'inline-block', width: 108, height: 12, verticalAlign: 'middle' }} />
                )}
              </span>
            </div>
          </Masthead>
          <hr className="rule-dashed" />
        </div>
      </header>

      <main className="wrap" id="main">
        <section className="hero" aria-label="Esta semana">
          <div className="hero-text">
            <p className="hero-eyebrow rv">eventos · festas · atividades</p>
            <h1 className="hero-title rv">
              Esta <em>semana</em> em Lisboa.
            </h1>
            <p className="hero-sub rv">
              Tudo o que acontece nos próximos sete dias. Organizado por tema, com filtros por dia &amp; preço.
            </p>
          </div>
          <div className="hero-controls">
            <SearchAndFilters
              query={query}
              setQuery={(v) => {
                setQuery(v);
                setFilters((f) => ({ ...f, q: v.trim().toLowerCase() }));
              }}
              filters={filters}
              week={week}
              onToggleDay={toggleDay}
              onSetFree={(free) => setFilters((f) => ({ ...f, free }))}
              onSetHideOngoing={(hideOngoing) => setFilters((f) => ({ ...f, hideOngoing }))}
            />
            <TopicChips week={week} taxonomy={taxonomy} filters={filters} onToggle={toggleTopic} />
          </div>
        </section>

        <hr className="rule-solid rv" />

        <section className="controls" aria-label="Filtros avançados">
          <div className="result-meta rv">
            <span id="result-count" aria-live="polite">
              {week ? `${visible.length} evento${visible.length === 1 ? '' : 's'}` : ''}
            </span>
            <span id="active-filters" className="muted">
              {activeFilterNote(deferredFilters)}
            </span>
          </div>
        </section>

        <section id="results" className="rv">
          {!week ? (
            <ResultsSkeleton />
          ) : (
            <Results visible={visible} taxonomy={taxonomy!} topicById={topicById} weekStart={week.week_start} />
          )}
        </section>

        {week && visible.length === 0 && (
          <div className="empty">
            <p>Nenhum evento corresponde aos filtros.</p>
            <button className="btn" type="button" onClick={clearAll}>
              Limpar filtros
            </button>
          </div>
        )}
      </main>

      <Footer week={week} />
    </>
  );
}

function activeFilterNote(f: Filters): string {
  const bits: string[] = [];
  if (f.topics.size) bits.push(`${f.topics.size} tema${f.topics.size > 1 ? 's' : ''}`);
  if (f.days.size) bits.push(`${f.days.size} dia${f.days.size > 1 ? 's' : ''}`);
  if (f.free) bits.push('só grátis');
  if (f.hideOngoing) bits.push('sem em curso');
  if (f.q) bits.push(`“${f.q}”`);
  return bits.length ? `· filtrado por ${bits.join(', ')}` : '';
}

function SearchAndFilters({
  query,
  setQuery,
  filters,
  week,
  onToggleDay,
  onSetFree,
  onSetHideOngoing,
}: {
  query: string;
  setQuery: (v: string) => void;
  filters: Filters;
  week: Week | null;
  onToggleDay: (d: (typeof DAYS)[number]) => void;
  onSetFree: (free: boolean) => void;
  onSetHideOngoing: (hide: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const weekStart = week?.week_start;

  return (
    <>
      <form className="searchbar rv" role="search" onSubmit={(e) => e.preventDefault()}>
        <label className="search-box">
          <SearchIcon />
          <span className="sr-only">Pesquisar</span>
          <input
            id="search"
            type="search"
            placeholder="Pesquisar eventos, locais, bairros…"
            autoComplete="off"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="filters-btn"
          aria-expanded={open}
          aria-controls="filter-collapse"
          onClick={() => setOpen((o) => !o)}
        >
          <span className="filt-sign" aria-hidden="true">
            {open ? '−' : '+'}
          </span>{' '}
          Filtros
        </button>
      </form>

      <div className={open ? 'filter-collapse open' : 'filter-collapse'} id="filter-collapse">
        <div className="filter-collapse-inner">
          <div className="filter-panel">
            <fieldset className="filter-group filter-day">
              <legend>Dia</legend>
              <div className="chips small">
                {DAYS.map((d, i) => {
                  const date = weekStart ? addDays(parseISO(weekStart), i) : null;
                  return (
                    <Chip key={d} pressed={filters.days.has(d)} onClick={() => onToggleDay(d)}>
                      <span>{DAY_LABELS[i]}{date ? ` ${format(date, 'd')}` : ''}</span>
                    </Chip>
                  );
                })}
              </div>
            </fieldset>

            <fieldset className="filter-group filter-price">
              <legend>Preço</legend>
              <div className="chips small">
                <Chip pressed={!filters.free} onClick={() => onSetFree(false)}>
                  <span>Todos</span>
                </Chip>
                <Chip pressed={filters.free} onClick={() => onSetFree(true)}>
                  <span>Só grátis</span>
                </Chip>
              </div>
            </fieldset>

            <fieldset className="filter-group filter-ongoing">
              <legend>Em curso</legend>
              <div className="chips small">
                <Chip pressed={!filters.hideOngoing} onClick={() => onSetHideOngoing(false)}>
                  <span>Mostrar</span>
                </Chip>
                <Chip pressed={filters.hideOngoing} onClick={() => onSetHideOngoing(true)}>
                  <span>Esconder</span>
                </Chip>
              </div>
            </fieldset>
          </div>
        </div>
      </div>
    </>
  );
}

function TopicChips({
  week,
  taxonomy,
  filters,
  onToggle,
}: {
  week: Week | null;
  taxonomy: Taxonomy | null;
  filters: Filters;
  onToggle: (id: string) => void;
}) {
  if (!week || !taxonomy) {
    return (
      <div className="chips rv" id="topic-chips" role="group" aria-label="Temas">
        <ChipSkeleton />
      </div>
    );
  }
  const counts: Record<string, number> = {};
  for (const ev of week.events) counts[ev.topic] = (counts[ev.topic] || 0) + 1;
  return (
    <div className="chips rv" id="topic-chips" role="group" aria-label="Temas">
      {taxonomy.topics.map((t) => {
        if (t.is_aggregator) return null;
        const n = counts[t.id] || 0;
        if (!n) return null;
        return (
          <Chip key={t.id} pressed={filters.topics.has(t.id)} onClick={() => onToggle(t.id)}>
            <span className="emoji">{t.emoji}</span>
            <span>{t.label}</span>
            <span className="count">{n}</span>
          </Chip>
        );
      })}
    </div>
  );
}

function Results({
  visible,
  taxonomy,
  topicById,
  weekStart,
}: {
  visible: EventItem[];
  taxonomy: Taxonomy;
  topicById: Record<string, Topic>;
  weekStart: string;
}) {
  const groups: Record<string, EventItem[]> = {};
  for (const ev of visible) (groups[ev.topic] ||= []).push(ev);

  let runningIndex = 0;
  const sections = [];
  for (const t of taxonomy.topics) {
    const list = groups[t.id];
    if (!list || !list.length) continue;
    list.sort((a, b) => (a.start || '').localeCompare(b.start || ''));
    const topic = topicById[t.id];
    if (!topic) continue;
    const firstIndex = runningIndex;
    runningIndex += list.length;
    sections.push(
      <TopicSection key={t.id} topic={topic} events={list} weekStart={weekStart} firstIndex={firstIndex} />,
    );
  }
  return <div className="results-inner">{sections}</div>;
}

function Footer({ week }: { week: Week | null }) {
  const stats = week
    ? `${week.event_count} eventos · ${week.source_count} fontes · atualizado a ${format(
        parseISO(week.generated_at),
        'dd/MM/yyyy',
      )}`
    : '';
  return (
    <footer className="site-footer">
      <div className="wrap">
        <hr className="rule-solid rv" />
        <div className="footer-row rv">
          <p className="footer-credit">
            Criado com ❤️ por <span>Nucabé</span>
          </p>
          <p id="footer-stats">{stats}</p>
        </div>
      </div>
    </footer>
  );
}
