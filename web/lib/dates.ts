// Date + external-URL helpers. All formatting is PT-PT.
import type { DayCode, EventItem } from '../types';

export const DAYS: DayCode[] = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
export const DAY_LABEL: Record<DayCode, string> = {
  mon: 'Seg',
  tue: 'Ter',
  wed: 'Qua',
  thu: 'Qui',
  fri: 'Sex',
  sat: 'Sáb',
  sun: 'Dom',
};
export const MONTHS = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];

export function fmtRange(startISO: string, endISO: string): string {
  const s = new Date(startISO + 'T00:00:00');
  const e = new Date(endISO + 'T00:00:00');
  const sameMonth = s.getMonth() === e.getMonth();
  return sameMonth
    ? `${s.getDate()}–${e.getDate()} ${MONTHS[e.getMonth()]} ${e.getFullYear()}`
    : `${s.getDate()} ${MONTHS[s.getMonth()]} – ${e.getDate()} ${MONTHS[e.getMonth()]} ${e.getFullYear()}`;
}

export function eventDate(ev: EventItem): Date {
  return new Date((ev.start || '').slice(0, 10) + 'T00:00:00');
}

// for a generic/unnamed venue (city-wide events, "TBA", "Secret Location"),
// exact coordinates beat a name search; otherwise the name shows the place card
export function mapsUrl(ev: EventItem): string {
  const generic = !ev.venue || /^(lisboa|tba|secret|local)/i.test(ev.venue);
  if (generic && ev.lat != null && ev.lng != null) {
    return 'https://www.google.com/maps/search/?api=1&query=' + ev.lat + ',' + ev.lng;
  }
  const q = [ev.venue, ev.neighbourhood, 'Lisboa'].filter(Boolean).join(', ');
  return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(q);
}

export function gcalUrl(ev: EventItem): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  let dates: string;
  if (!ev.all_day && (ev.start || '').length > 10) {
    const fmt = (d: Date) =>
      `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`;
    const s = new Date(ev.start);
    const e = new Date(s.getTime() + 2 * 3600e3); // no published end time — assume 2h
    dates = `${fmt(s)}/${fmt(e)}`;
  } else {
    // all-day (Google wants an EXCLUSIVE end date)
    const end = new Date((ev.end || ev.start).slice(0, 10) + 'T00:00:00');
    end.setDate(end.getDate() + 1);
    dates =
      `${ev.start.slice(0, 10).replace(/-/g, '')}/` +
      `${end.getFullYear()}${pad(end.getMonth() + 1)}${pad(end.getDate())}`;
  }
  const p = new URLSearchParams({
    action: 'TEMPLATE',
    text: ev.title,
    dates,
    details: ev.url || '',
    location: [ev.venue, ev.neighbourhood, 'Lisboa'].filter(Boolean).join(', '),
    ctz: 'Europe/Lisbon',
  });
  return 'https://calendar.google.com/calendar/render?' + p.toString();
}

export function slugify(s: string): string {
  const map: Record<string, string> = {
    á: 'a', à: 'a', ã: 'a', â: 'a', é: 'e', ê: 'e', í: 'i',
    ó: 'o', ô: 'o', õ: 'o', ú: 'u', ç: 'c', '&': 'and',
  };
  s = (s || '').toLowerCase().replace(/[áàãâéêíóôõúç&]/g, (ch) => map[ch] || ch);
  return s.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'x';
}
