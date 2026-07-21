import { useState } from 'react';
import type { CSSProperties } from 'react';
import type { EventItem, Topic } from '../types';
import { DAY_LABEL, MONTHS, eventDate, gcalUrl, mapsUrl } from '../lib/dates';
import { CalendarIcon, PinIcon } from './icons';

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

  const d = eventDate(ev);
  const endISO = (ev.end || '').slice(0, 10);
  const multiDay = Boolean(endISO && endISO > (ev.start || '').slice(0, 10));

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
    const endD = ev.end ? new Date(ev.end.slice(0, 10) + 'T00:00:00') : null;
    const ref = new Date(weekStart + 'T00:00:00');
    const showMonth = endD && (endD.getMonth() !== ref.getMonth() || endD.getFullYear() !== ref.getFullYear());
    return (
      <div className="when">
        <span className="day">{endD ? 'até' : ev.days[0] ? DAY_LABEL[ev.days[0]] : 'sem'}</span>
        <span className="date">
          {(endD || d).getDate() || ''}
          {showMonth && <span className="date-mon"> {MONTHS[endD!.getMonth()] ?? ''}</span>}
        </span>
        <span className="ongoing">{ev.ongoing ? 'em curso' : ''}</span>
      </div>
    );
  }
  const time = (ev.start || '').slice(11, 16);
  return (
    <div className="when">
      <span className="day">{ev.days[0] ? DAY_LABEL[ev.days[0]] : ''}</span>
      <span className="date">{d.getDate() || ''}</span>
      <span className="time">{time || '—'}</span>
    </div>
  );
}
