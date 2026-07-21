import type { EventItem, Mock, Taxonomy, Week } from '../types';

// The canonical open-data document the site fetches (public/data.jsonld):
// schema.org/Event with a `plx:` extension namespace for the domain fields.
// We type only the parts the UI reads back; scripts/build-open-data.mjs owns
// the forward mapping (and guards it with a round-trip assertion).

interface LdEvent {
  identifier: string;
  name: string;
  description?: string;
  startDate: string;
  endDate?: string;
  url?: string;
  image?: string;
  inLanguage?: string[];
  location?: {
    name?: string;
    address?: { addressLocality?: string };
    geo?: { latitude: number; longitude: number };
  };
  performer?: { name: string }[];
  'plx:topic': string;
  'plx:categories': number[];
  'plx:zone'?: string;
  'plx:days': EventItem['days'];
  'plx:ongoing': boolean;
  'plx:allDay': boolean;
  'plx:price'?: EventItem['price'];
}

export interface LdDataset {
  'plx:week': Omit<Week, 'events'>;
  'plx:taxonomy': Taxonomy;
  'plx:events': LdEvent[];
}

function toEventItem(e: LdEvent): EventItem {
  return {
    id: e.identifier,
    title: e.name,
    topic: e['plx:topic'],
    categories: e['plx:categories'],
    venue: e.location?.name ?? '',
    neighbourhood: e.location?.address?.addressLocality ?? null,
    zone: e['plx:zone'] ?? null,
    lat: e.location?.geo?.latitude ?? null,
    lng: e.location?.geo?.longitude ?? null,
    start: e.startDate,
    end: e.endDate ?? null,
    all_day: e['plx:allDay'],
    ongoing: e['plx:ongoing'],
    days: e['plx:days'],
    price: e['plx:price'] ?? null,
    language: e.inLanguage ?? null,
    url: e.url ?? null,
    description: e.description ?? null,
    image: e.image ?? null,
    lineup: e.performer ? e.performer.map((p) => p.name) : null,
  };
}

export function adaptDataset(doc: LdDataset): Mock {
  return {
    taxonomy: doc['plx:taxonomy'],
    week: { ...doc['plx:week'], events: doc['plx:events'].map(toEventItem) },
  };
}
