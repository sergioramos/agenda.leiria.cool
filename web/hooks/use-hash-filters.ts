// Shareable filter state in the URL hash, ported from docs/assets/app.js
// (loadHash/syncHash). Reads the hash once on mount, then debounces writes back
// (history.replaceState is rate-limited in some browsers — the debounce guards
// it). Returns the filter state plus a setter.
import { useEffect, useRef, useState } from 'react';
import type { DayCode, Filters } from '../types';

function readHash(): Filters {
  const p = new URLSearchParams(location.hash.slice(1));
  return {
    q: (p.get('q') || '').toLowerCase(),
    topics: new Set((p.get('t') || '').split(',').filter(Boolean)),
    days: new Set((p.get('d') || '').split(',').filter(Boolean) as DayCode[]),
    free: p.get('free') === '1',
    hideOngoing: p.get('nc') === '1',
  };
}

function writeHash(f: Filters): void {
  const p = new URLSearchParams();
  if (f.q) p.set('q', f.q);
  if (f.topics.size) p.set('t', [...f.topics].join(','));
  if (f.days.size) p.set('d', [...f.days].join(','));
  if (f.free) p.set('free', '1');
  if (f.hideOngoing) p.set('nc', '1');
  const s = p.toString();
  history.replaceState(null, '', s ? '#' + s : location.pathname);
}

export function useHashFilters(): {
  filters: Filters;
  setFilters: React.Dispatch<React.SetStateAction<Filters>>;
} {
  const [filters, setFilters] = useState<Filters>(readHash);
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return; // don't rewrite the hash we just read
    }
    const t = setTimeout(() => writeHash(filters), 150);
    return () => clearTimeout(t);
  }, [filters]);

  return { filters, setFilters };
}
