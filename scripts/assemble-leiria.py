#!/usr/bin/env python3
"""Assemble the final data/mock.json from the Leiria scrape + taxonomy judgment.

Inputs:
  data/leiria-events.json    raw scrape (scripts/scrape-leiria.py)
  data/leiria-judgment.json  {catmap:{map:[...]}, hoods:{neighbourhoods:[...]}}
  data/mock.json             existing file (only topics + categories defs kept)

Drops every Lisbon leftover: old neighbourhoods, old events. Adds a `venues`
list inferred deterministically from the scrape (name + its locality). Keeps the
topic/category taxonomy (not Lisbon-specific).
"""
import datetime as dt
import html
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    p = ROOT / "data" / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"events": [], "distinct_venues": []}


# merge upcoming + past archive (upcoming first so it wins on any slug collision)
_sources = [_load("leiria-upcoming.json"), _load("leiria-events.json")]
_seen, _events = set(), []
for _src in _sources:
    for _e in _src["events"]:
        if _e["slug"] in _seen:
            continue
        _seen.add(_e["slug"])
        _events.append(_e)
_venue_counts = {}
for _src in _sources:
    for _v in _src["distinct_venues"]:
        _k = (_v["venue"], _v["localidade"] or "")
        _venue_counts[_k] = _venue_counts.get(_k, 0) + _v["count"]
raw = {
    "events": _events,
    "distinct_venues": [{"venue": k[0], "localidade": k[1], "count": n} for k, n in _venue_counts.items()],
}

judg = json.loads((ROOT / "data/leiria-judgment.json").read_text(encoding="utf-8"))
mock = json.loads((ROOT / "data/mock.json").read_text(encoding="utf-8"))

GENERIC = {"cultura", "eventos", "evento"}  # skip when a specific label is present


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(s or "")).strip()


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def aliases_for(name):
    low = name.lower()
    return sorted({low, strip_accents(low)})


# ---- taxonomy judgment lookups -------------------------------------------
catmap = {}  # lowercased label -> (topic, [cat ids])
for e in judg["catmap"]["map"]:
    catmap[e["label"].lower()] = (e["topic"], e["categories"])

hoods = judg["hoods"]["neighbourhoods"]
hood_zone = {clean(h["name"]): h["zone"] for h in hoods}
# locality (freguesia) name -> canonical neighbourhood name, matched by accent-folded key
hood_by_key = {strip_accents(clean(h["name"]).lower()): clean(h["name"]) for h in hoods}


def label_topic(labels):
    """Pick topic from the most specific label; union all matched category ids."""
    labels = [clean(x) for x in labels if clean(x)]
    chosen, cats = None, []
    for lab in labels:
        hit = catmap.get(lab.lower())
        if not hit:
            continue
        cats += hit[1]
        if chosen is None and lab.lower() not in GENERIC:
            chosen = hit[0]
    if chosen is None:  # all generic (or unmapped) -> first mapped label's topic
        for lab in labels:
            if lab.lower() in catmap:
                chosen = catmap[lab.lower()][0]
                break
    if chosen is None:
        chosen, cats = "guides", [49]
    seen, uniq = set(), []
    for c in cats:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return chosen, uniq or [49]


def neighbourhood_for(localidade):
    if not localidade:
        return None
    return hood_by_key.get(strip_accents(clean(localidade).lower()))


# ---- venues (deterministic: name + its locality) --------------------------
venue_loc = {}  # canonical venue name -> {locality-count}
for v in raw["distinct_venues"]:
    name = clean(v["venue"])
    if not name:
        continue
    venue_loc.setdefault(name, {})
    loc = clean(v["localidade"])
    venue_loc[name][loc] = venue_loc[name].get(loc, 0) + v["count"]

venues = []
venue_hood = {}  # canonical venue name -> neighbourhood (for backfilling events)
for name in sorted(venue_loc):
    loc = max(venue_loc[name].items(), key=lambda kv: kv[1])[0] if venue_loc[name] else ""
    hood = neighbourhood_for(loc)
    venue_hood[name] = hood
    venues.append({
        "name": name,
        "neighbourhood": hood,
        "aliases": aliases_for(name),
    })

# ---- events ---------------------------------------------------------------
def to_time(t):
    m = re.search(r"(\d{1,2})h(\d{2})?", t or "")
    return f"{int(m.group(1)):02d}:{m.group(2) or '00'}" if m else None


events = []
dates = []
for r in raw["events"]:
    y, mo, d = r["year"], r["month"], r["day"]
    hhmm = to_time(r["time"])
    start = f"{y:04d}-{mo:02d}-{d:02d}" + (f"T{hhmm}" if hhmm else "")
    dates.append(f"{y:04d}-{mo:02d}-{d:02d}")
    topic, cats = label_topic(r["categories_raw"])
    venue = clean(r["venue"])
    # locality is the primary signal; fall back to the venue's known neighbourhood
    hood = neighbourhood_for(r["localidade"]) or venue_hood.get(venue)
    events.append({
        "id": r["id"],
        "title": clean(r["title"]),
        "topic": topic,
        "categories": cats,
        "venue": venue,
        "neighbourhood": hood,
        "zone": hood_zone.get(hood),
        "lat": None,
        "lng": None,
        "start": start,
        "end": None,
        "all_day": hhmm is None,
        "ongoing": False,
        "days": [r["weekday"]] if r["weekday"] else [],
        "price": None,
        "language": ["pt"],
        "url": r["url"],
        "description": None,
        "image": r["image"],
        "lineup": None,
    })

# ---- write final mock ------------------------------------------------------
mock["taxonomy"]["neighbourhoods"] = hoods
mock["taxonomy"]["venues"] = venues
mock["week"] = {
    "week_start": min(dates),
    "week_end": max(dates),
    "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "is_sample": True,
    "source_count": 1,
    "event_count": len(events),
    "events": events,
}

(ROOT / "data/mock.json").write_text(json.dumps(mock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# sanity report
unmapped = sorted({clean(l).lower() for r in raw["events"] for l in r["categories_raw"]} - set(catmap))
no_hood = sum(1 for e in events if e["neighbourhood"] is None)
print(f"ok · {len(events)} events · {len(venues)} venues · {len(hoods)} neighbourhoods")
print(f"   unmapped category labels: {unmapped or 'none'}")
print(f"   events with no matched neighbourhood: {no_hood}")
