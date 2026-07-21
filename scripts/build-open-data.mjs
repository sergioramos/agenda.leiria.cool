// Regenerates the open-data artifacts from the internal event dump.
//
//   data/mock.json    (internal shape produced by the crawler; not served)
//        │
//        ▼
//   public/data.jsonld   canonical open data: schema.org/Event + a `plx:`
//                        extension namespace carrying the domain fields
//                        (topic, categories, days, ongoing…) the UI needs.
//   datapackage.json     Frictionless descriptor (repo root).
//   public/dcat.jsonld   DCAT catalogue record (served with the data).
//
// The site reads data.jsonld directly (see web/lib/jsonld.ts), so this script
// also round-trips every event back to the internal shape and asserts nothing
// was lost before writing anything.
//
// Run: npm run data   (BASE_URL overrides the published origin).

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import assert from 'node:assert/strict';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const BASE = (process.env.BASE_URL || 'https://sergioramos.github.io/pregoeiro/').replace(/\/*$/, '/');
const LICENSE = 'https://opendatacommons.org/licenses/odbl/1-0/';

const src = JSON.parse(readFileSync(resolve(ROOT, 'data/mock.json'), 'utf8'));
const { taxonomy, week } = src;
const { events, ...weekMeta } = week;

// internal event -> schema.org/Event (+ plx: domain fields)
function toLd(e) {
  const ev = { '@type': 'Event', '@id': `${BASE}event/${e.id}`, identifier: e.id, name: e.title };
  // `!= null` (not truthiness) so empty strings/arrays survive the round-trip.
  if (e.description != null) ev.description = e.description;
  ev.startDate = e.start;
  if (e.end != null) ev.endDate = e.end;
  if (e.url != null) ev.url = e.url;
  if (e.image != null) ev.image = e.image;
  if (e.language != null) ev.inLanguage = e.language;

  const place = { '@type': 'Place' };
  if (e.venue) place.name = e.venue;
  if (e.neighbourhood != null)
    place.address = { '@type': 'PostalAddress', addressLocality: e.neighbourhood, addressRegion: 'Leiria' };
  if (e.lat != null && e.lng != null)
    place.geo = { '@type': 'GeoCoordinates', latitude: e.lat, longitude: e.lng };
  if (Object.keys(place).length > 1) ev.location = place;

  // schema.org offer for consumers; the lossless price lives in plx:price below.
  if (e.price) {
    const offer = { '@type': 'Offer', priceCurrency: e.price.currency || 'EUR', availability: 'https://schema.org/InStock' };
    if (e.price.min != null) offer.price = String(e.price.min);
    ev.offers = offer;
  }
  if (e.lineup != null) ev.performer = e.lineup.map((name) => ({ '@type': 'PerformingGroup', name }));

  ev['plx:topic'] = e.topic;
  ev['plx:categories'] = e.categories;
  if (e.zone != null) ev['plx:zone'] = e.zone;
  ev['plx:days'] = e.days;
  ev['plx:ongoing'] = e.ongoing;
  ev['plx:allDay'] = e.all_day;
  if (e.price) ev['plx:price'] = e.price;
  return ev;
}

// reverse of toLd — mirrors web/lib/jsonld.ts; used only for the round-trip check.
function fromLd(e) {
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

const ldEvents = events.map(toLd);

// guard: the site rebuilds the internal shape from data.jsonld, so the mapping
// must be lossless for every field the UI touches.
assert.deepEqual(ldEvents.map(fromLd), events, 'JSON-LD round-trip lost event data');

const dataset = {
  '@context': { '@vocab': 'https://schema.org/', plx: `${BASE}ns#` },
  '@type': 'Dataset',
  '@id': `${BASE}data.jsonld`,
  name: 'Pregoeiro — eventos desta semana em Leiria',
  description:
    'Dataset semanal aberto dos eventos culturais de Leiria, como schema.org/Event em JSON-LD. Campos de domínio (tema, categorias, dias, em curso) no namespace plx:.',
  url: BASE,
  license: LICENSE,
  creator: { '@type': 'Organization', name: 'Sérgio Ramos' },
  inLanguage: 'pt-PT',
  temporalCoverage: `${weekMeta.week_start}/${weekMeta.week_end}`,
  dateModified: weekMeta.generated_at,
  'plx:week': weekMeta,
  'plx:taxonomy': taxonomy,
  'plx:events': ldEvents,
};

const datapackage = {
  name: 'pregoeiro',
  title: 'Pregoeiro — Esta Semana em Leiria',
  description: 'Weekly open dataset of cultural events in Leiria, published as schema.org/Event in JSON-LD.',
  homepage: BASE,
  version: weekMeta.generated_at,
  licenses: [{ name: 'ODbL-1.0', title: 'Open Database License v1.0', path: LICENSE }],
  resources: [
    {
      name: 'events',
      title: 'Events (schema.org/Event, JSON-LD)',
      path: 'public/data.jsonld',
      format: 'jsonld',
      mediatype: 'application/ld+json',
      encoding: 'utf-8',
    },
  ],
};

const dcat = {
  '@context': {
    dcat: 'http://www.w3.org/ns/dcat#',
    dct: 'http://purl.org/dc/terms/',
    foaf: 'http://xmlns.com/foaf/0.1/',
  },
  '@type': 'dcat:Dataset',
  '@id': `${BASE}dcat.jsonld#dataset`,
  'dct:title': 'Pregoeiro — Esta Semana em Leiria',
  'dct:description': 'Weekly open dataset of cultural events in Leiria (schema.org/Event, JSON-LD).',
  'dct:license': { '@id': LICENSE },
  'dct:modified': weekMeta.generated_at,
  'dct:temporal': `${weekMeta.week_start}/${weekMeta.week_end}`,
  'dct:publisher': { '@type': 'foaf:Agent', 'foaf:name': 'Sérgio Ramos' },
  'dcat:landingPage': { '@id': BASE },
  'dcat:distribution': [
    {
      '@type': 'dcat:Distribution',
      '@id': `${BASE}data.jsonld`,
      'dct:title': 'JSON-LD (schema.org/Event)',
      'dcat:accessURL': { '@id': `${BASE}data.jsonld` },
      'dcat:downloadURL': { '@id': `${BASE}data.jsonld` },
      'dcat:mediaType': 'application/ld+json',
      'dct:format': 'JSON-LD',
      'dct:license': { '@id': LICENSE },
    },
  ],
};

const write = (rel, obj) => writeFileSync(resolve(ROOT, rel), JSON.stringify(obj, null, 2) + '\n');
write('public/data.jsonld', dataset);
write('datapackage.json', datapackage);
write('public/dcat.jsonld', dcat);

console.log(`ok · ${ldEvents.length} events → public/data.jsonld, datapackage.json, public/dcat.jsonld`);
