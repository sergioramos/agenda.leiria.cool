// Shared data shapes for the UI. Interfaces/type aliases only — inherently
// erasable, so they satisfy Node's type stripping (erasableSyntaxOnly). The UI
// reads a single static fixture, web/public/mock.json (the `Mock` shape).

export type DayCode = 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun';

export interface Topic {
  id: string;
  label: string;
  emoji: string;
  categories: number[];
  is_aggregator?: boolean;
}
export interface Neighbourhood {
  name: string;
  zone: string;
  aliases?: string[];
}
export interface Taxonomy {
  topics: Topic[];
  categories: Record<string, string>;
  neighbourhoods: Neighbourhood[];
}

export interface EventPrice {
  is_free: boolean;
  min: number | null;
  currency: string;
  text: string;
}
export interface EventItem {
  id: string;
  title: string;
  topic: string;
  categories: number[];
  venue: string;
  source_id?: string;
  source?: string;
  neighbourhood: string | null;
  zone: string | null;
  lat: number | null;
  lng: number | null;
  start: string;
  end: string | null;
  all_day: boolean;
  ongoing: boolean;
  days: DayCode[];
  price: EventPrice | null;
  language: string[] | null;
  url: string | null;
  description: string | null;
  image: string | null;
  lineup: string[] | null;
  links?: unknown;
  prov?: unknown;
}
export interface Week {
  week_start: string;
  week_end: string;
  generated_at: string;
  is_sample?: boolean;
  source_count: number;
  event_count: number;
  events: EventItem[];
}

// The single static fixture the UI fetches (web/public/mock.json).
export interface Mock {
  taxonomy: Taxonomy;
  week: Week;
}

// Public-page filter state (shared between App and the hash-sync hook).
export interface Filters {
  q: string;
  topics: Set<string>;
  days: Set<DayCode>;
  free: boolean;
  hideOngoing: boolean;
}
