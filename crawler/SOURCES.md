# Sources, recovery & known limits

How events are pulled, how images/venues/dates are repaired, what *cannot* be
fixed, and how you find out when a source breaks. Read this before "fixing" a
source that shows 0% on the admin **Qualidade por fonte** wall — some 0%s are
permanent and expected.

The code lives in `crawler/`; this file is the intent + the failure modes, not a
line-by-line mirror. If you change behaviour, update the relevant section.

## Two tracks

- **Structured connectors** (`connectors.py`, run by `fetch_connectors.py`): ~10
  sites with clean JSON/HTML APIs, pulled over a 75-day horizon into the pool
  (`docs/data/pool.json`). No AI, no cost. Each returns `(events, status)` and
  never raises.
- **Long-tail HTML + AI** (`crawl_events.py`): the ~500 venue sites. Feeds (ICS)
  first; AI extraction only when there's no feed. Cost-capped.

`merge_week.py` joins both into the published week.

## Image recovery (no AI)

An event's poster is found, in order, by `core.scrape_event_page`:
1. JSON-LD `Event.image`.
2. `og:image`.
3. **content-image fallback** (`_content_image`): first same-site image under a
   `/uploads|/media|/files/` path, skipping logos/thumbs/crops. Backstopped by
   `drop_shared_images` (an image shared across differently-titled events = a
   default/logo, nulled).

Plus:
- **`_canonical_img`**: rewrites a `*.elasticbeanstalk.com` origin host (broken
  TLS, e.g. Visit Lisboa) to the page's public host. No-op if absent.
- **`core.eventon_events`**: EventON sites (Hot Clube) repeat all events' JSON-LD
  on every page with broken dates, so the per-event image is read from the
  listing microdata instead. Returns `[]` if the markup changes (falls back to
  the chain above).
- **`_good_img`**: rejects logos/favicons/banners/icons as whole path tokens
  (so "diá**logo**" and `/events/**banners**/` survive, `/logos/` doesn't).

Where it runs every crawl:
- connectors → `connectors.recover_images` (in `fetch_connectors.py`).
- HTML track → `crawl_events.enrich_events` (reads each event page + the listing
  for homepage-only URLs).

### Images that CANNOT be recovered (expected 0% on the wall)

| Reason | Examples | Why |
|---|---|---|
| **Cloudflare** bot block (403) | Tickettailor — Black Cat Cinema (`app.tickettailor.com` *and* the `tickets.<venue>` white-label) | The image exists and a real browser sees it, but Cloudflare 403s any plain HTTP client regardless of headers. Our crawler is `requests`-based with no JS engine (deliberate: cheap, runs in CI). Reaching these needs a headless browser (Playwright — heavy/slow/flaky) or TLS-fingerprint evasion (an anti-bot arms race that breaks unpredictably). Both are out of scope by choice. |
| Page returns **403** / no image field | Resident Advisor | RA's HTML 403s; its images come only from the GraphQL `FLYERFRONT`, which some events lack. |
| **JS-only** listing (Wix/SPA) | Black Cat Cinema's own site | Event data/images are rendered client-side; the static HTML has neither. |
| API/page carries no image | some AgendaLX/BOL rows | Source never published one. |
| Page has only a **shared** banner/logo | underdogs, little-big-apple, … | The only image found is reused across the source's events, so `drop_shared_images` nulls it (better than the same wrong banner on every card). |

These are structural ceilings, not bugs. The wall shows them so you are not
surprised; do not spend time "fixing" them unless the source itself changes, or
unless you decide to add browser-based fetching (a real project, not a patch).

## Venue names & duplicates

Cross-source copies of one event are merged in `merge_week.py`, in this order
(all before `dedupe`):
1. **`apply_venue_aliases`** — `sources/venue_aliases.json`, a hand-curated map
   of spellings that are the same place (MAC/CCB→Centro Cultural de Belém,
   CAM→Gulbenkian, Tejo Park/"Rock in Rio Lisboa"→Parque Tejo, …). Fills the
   canonical coordinate so a source that sent no location still lines up.
   **To add one**: edit that file (`aliases` + a `coords` entry). Keep it
   conservative and one-directional (CAM→Gulbenkian, never the reverse).
2. **`collapse_daily_runs`** — merges same-title/venue runs whose dates overlap
   or are adjacent (an API returning one entry per open day, a nightly show, two
   sources' overlapping spans). A series with real date *gaps* stays separate.
   `title_core` strips a trailing year / "– DD de mês" so a festival's per-day
   titles group ("Rock in Rio Lisboa 2026" / "… – 27 de Junho").
3. **`canonicalize_venue_coords`** — within a ~110m cell, unify spelling variants
   that share a distinctive token; a different venue in the same cell is left
   alone.
4. **`canonicalize_venues`** — exact match to the venue directory only (never
   fuzzy, so "Lisboa" can't become "@esnlisboa").
5. **`dedupe`** — same place + similar title (ratio, prefix, or containment).

### What this does NOT catch (residual duplicates are expected)

- Two sources using genuinely different venue strings with no shared coords and
  no alias (e.g. an aggregator's Instagram handle "@esnlisboa"). Add an alias if
  it's a real recurring pair.
- Word-order title variants of the same event ("a Coleção Interminável" vs "a
  Interminável Coleção") on different start dates.

`venue_aliases.json` can go **stale** (a venue renamed at the source) — the
alias silently stops matching and the duplicate reappears. You see this on the
wall (a cluster grows); fix it by updating the alias.

## How you find out a source broke

Three layers — the answer to "I don't want things breaking without telling me":
1. **Connector shrink detection** (`connector_state.json`): a connector that
   returns < 50% of its rolling median is marked `shrunk` on the admin **Fontes**
   wall.
2. **Reachability** (`check_sources.py`, Monday): unreachable sources accumulate
   `dead_signals` in `sources.json` and are proposed for review.
3. **Per-source quality wall** (admin · Estado · *Qualidade por fonte*): events,
   % image / coords / price and a duplicate flag per source. A source that
   starts returning fewer events, or drops to 0% image/coords, shows here.
   Layers 1–2 cover the ~10 connectors automatically; the quality wall is the
   manual check for the ~500 HTML sources.

## Dates

`make_event` flags an event `ongoing` ("em curso") when it started before the
week or runs ≥5 days. Connectors that clamp a long run's start to the window for
sorting pass `ongoing=True` so the flag isn't lost. The card shows "até <end>"
for any multi-day run (the frontend shows a range even when not flagged ongoing).
