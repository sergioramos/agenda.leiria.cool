import type { EventItem, Topic } from '../types';
import EventCard from './event-card';

// One topic group: kicker + heading + its cards. `firstIndex` is the running
// global card index so the entrance cascade (--d) stays in document order
// across sections.
export default function TopicSection({
  topic,
  events,
  weekStart,
  firstIndex,
}: {
  topic: Topic;
  events: EventItem[];
  weekStart: string;
  firstIndex: number;
}) {
  const parts = topic.label.split(' & ');
  return (
    <section className="topic-section">
      <div className="topic-head">
        <div className="topic-head-text">
          <span className="topic-kicker">
            {events.length} {events.length === 1 ? 'evento' : 'eventos'}
          </span>
          <h2>
            {parts.map((part, i) => (
              <span key={i}>
                {i > 0 && <span className="amp"> &amp; </span>}
                {part}
              </span>
            ))}
          </h2>
        </div>
      </div>
      <div className="cards">
        {events.map((ev, i) => (
          <EventCard key={ev.id} ev={ev} topic={topic} weekStart={weekStart} index={firstIndex + i} />
        ))}
      </div>
    </section>
  );
}
