import { useState } from 'react';
import type { CSSProperties } from 'react';
import { addDays, addHours, format, isAfter, isSameMonth, isValid, parseISO } from 'date-fns';
import { pt } from 'date-fns/locale';
import type { EventItem, Topic } from '../types';
import { CalendarIcon, PinIcon } from './icons';

// for a generic/unnamed venue (city-wide events, "TBA", "Secret Location"),
// exact coordinates beat a name search; otherwise the name shows the place card
function mapsUrl(ev: EventItem): string {
  const generic = !ev.venue || /^(lisboa|tba|secret|local)/i.test(ev.venue);
  if (generic && ev.lat != null && ev.lng != null) {
    return 'https://www.google.com/maps/search/?api=1&query=' + ev.lat + ',' + ev.lng;
  }
  const q = [ev.venue, ev.neighbourhood, 'Lisboa'].filter(Boolean).join(', ');
  return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(q);
}

function gcalUrl(ev: EventItem): string {
  let dates: string;
  if (!ev.all_day && (ev.start || '').length > 10) {
    const s = parseISO(ev.start);
    const e = addHours(s, 2); // no published end time — assume 2h
    const fmt = (d: Date) => format(d, "yyyyMMdd'T'HHmmss");
    dates = `${fmt(s)}/${fmt(e)}`;
  } else {
    // all-day (Google wants an EXCLUSIVE end date)
    const start = parseISO(ev.start.slice(0, 10));
    const end = addDays(parseISO((ev.end || ev.start).slice(0, 10)), 1);
    dates = `${format(start, 'yyyyMMdd')}/${format(end, 'yyyyMMdd')}`;
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

// Ported 1:1 from card() in docs/assets/app.js. `weekStart` is the viewed
// week's Monday (for the multi-day "até 12 jul" month disambiguation); `index`
// drives the staggered entrance (--d, capped at 10 so the tail follows quickly).
export default function EventCard({
  ev,
  topic,
  weekStart,
  index,
}: {
  ev: EventItem;
  topic: Topic | undefined;
  weekStart: string;
  index: number;
}) {
  // a broken/blank/tiny image (logo pixel, icon) → fall back to the topic emoji
  const [showImage, setShowImage] = useState(Boolean(ev.image));
  const emoji = topic?.emoji || '📌';

  const d = parseISO((ev.start || '').slice(0, 10));
  const multiDay = Boolean(
    ev.end && isAfter(parseISO(ev.end.slice(0, 10)), parseISO((ev.start || '').slice(0, 10))),
  );

  const style: CSSProperties = { ['--d' as string]: `${Math.min(index, 10) * 110}ms` };

  return (
    <article className="card card-rv" style={style}>
      <div className="card-media">
        <div className={showImage ? 'card-img' : 'card-img ph'} aria-hidden="true">
          {showImage && ev.image ? (
            <img
              src={ev.image}
              alt=""
              loading="lazy"
              width={96}
              height={96}
              onError={() => setShowImage(false)}
              onLoad={(e) => {
                const img = e.currentTarget;
                if (img.naturalWidth < 64 || img.naturalHeight < 64) setShowImage(false);
              }}
            />
          ) : (
            emoji
          )}
        </div>
      </div>

      <div className="body">
        <div className="card-toprow">
          <div className="card-text">
            <h3>
              {ev.url ? (
                <a href={ev.url} target="_blank" rel="noopener">
                  {ev.title}
                </a>
              ) : (
                <span className="no-link">{ev.title}</span>
              )}
            </h3>
            <a
              className="venue-line"
              href={mapsUrl(ev)}
              target="_blank"
              rel="noopener"
              title="Abrir no Google Maps"
            >
              <PinIcon />
              <span>{[ev.venue, ev.neighbourhood].filter(Boolean).join(' · ')}</span>
            </a>
          </div>
          <a
            className="save-date"
            href={gcalUrl(ev)}
            target="_blank"
            rel="noopener"
            title="Adicionar ao Google Calendar"
          >
            <CalendarIcon />
            <span>Guardar data</span>
          </a>
        </div>

        <div className="badges">
          {topic && <span className="badge">{topic.label}</span>}
          {ev.price?.is_free ? (
            <span className="badge free">Grátis</span>
          ) : ev.price?.text ? (
            <span className="badge">{ev.price.text}</span>
          ) : null}
          {(ev.language || []).includes('en') && <span className="badge">EN</span>}
        </div>
      </div>

      <span className="card-divider" aria-hidden="true" />

      <When ev={ev} d={d} multiDay={multiDay} weekStart={weekStart} />
    </article>
  );
}

function When({
  ev,
  d,
  multiDay,
  weekStart,
}: {
  ev: EventItem;
  d: Date;
  multiDay: boolean;
  weekStart: string;
}) {
  // a run spanning more than one day shows its closing day ("até 26"), even when
  // it isn't flagged "em curso"; the "em curso" label stays tied to ev.ongoing.
  if (ev.ongoing || multiDay) {
    const endD = ev.end ? parseISO(ev.end.slice(0, 10)) : null;
    const ref = parseISO(weekStart);
    const showMonth = endD && !isSameMonth(endD, ref);
    return (
      <div className="when">
        <span className="day">{endD ? 'até' : isValid(d) ? format(d, 'EEE', { locale: pt }) : 'sem'}</span>
        <span className="date">
          {isValid(endD || d) ? format(endD || d, 'd') : ''}
          {showMonth && <span className="date-mon"> {format(endD!, 'MMM', { locale: pt })}</span>}
        </span>
        <span className="ongoing">{ev.ongoing ? 'em curso' : ''}</span>
      </div>
    );
  }
  // date-only starts (all-day) carry no time — keep the "—" fallback
  const time = (ev.start || '').length > 10 ? format(parseISO(ev.start), 'HH:mm') : '';
  return (
    <div className="when">
      <span className="day">{isValid(d) ? format(d, 'EEE', { locale: pt }) : ''}</span>
      <span className="date">{isValid(d) ? format(d, 'd') : ''}</span>
      <span className="time">{time || '—'}</span>
    </div>
  );
}
