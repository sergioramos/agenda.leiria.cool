"""
Structured-source connectors for the Pregoeiro crawler.

Each connector pulls events from a site that exposes a clean JSON API (no AI, no
HTML scraping) over a WIDE horizon, so the weekly publish can just filter the
persistent pool. A connector returns (events, status) where status is one of
{ok, partial, failed}; it NEVER raises and NEVER returns None — a dead endpoint
yields ([], "failed") so the rest of the run is untouched.

Connectors are a small hard-coded registry of PARAMETERISED fetcher types
(not one module per source, not a generic config-map framework). Today:
  - agendalx : the CM Lisboa official spine (custom wp-json/agendalx/v1 namespace)
  - tribe    : The Events Calendar REST (CCB; date-filterable, very clean)
  - gulbenkian: WordPress v2 events with the Gulbenkian session/ticket shape

Events are normalised through core.make_event, so titles/prices/urls get the
same cleaning as the rest of the pipeline. The venue name comes from the API
and is matched back to the seed list to inherit a neighbourhood when known.
"""
from __future__ import annotations
import html as _html
import json
import re
import time
import unicodedata
from datetime import date, timedelta

import core


# ---------- registry ----------
# `topic` is the source-level fallback/aggregator flag for dedupe; the real
# per-event topic is mapped from the payload. AgendaLX is an aggregator (lists
# many venues), so topic="guides"; CCB/Gulbenkian are single official venues.
CONNECTORS = [
    {
        "id": "agendalx", "type": "agendalx", "name": "AgendaLX",
        "website": "https://www.agendalx.pt",
        "api": "https://www.agendalx.pt/wp-json/agendalx/v1/events",
        "topic": "guides", "neighbourhood": None, "zone": None,
    },
    {
        "id": "ccb", "type": "tribe", "name": "Centro Cultural de Belém",
        "website": "https://www.ccb.pt",
        "api": "https://www.ccb.pt/wp-json/tribe/events/v1/events",
        "topic": "art", "neighbourhood": "Belém", "zone": "city",
    },
    {
        "id": "gulbenkian", "type": "gulbenkian", "name": "Fundação Calouste Gulbenkian",
        "website": "https://gulbenkian.pt",
        "api": "https://www.gulbenkian.pt/wp-json/wp/v2/events",
        "topic": "music", "neighbourhood": "Avenidas Novas", "zone": "city",
    },
]


# ---------- helpers ----------
def _fold(s: str) -> str:
    """Lowercase, accent-folded — for keyword/topic matching (keeps spaces)."""
    f = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in f if not unicodedata.combining(c)).lower()


_TOPIC_KW = [
    ("film",        ["cinema", "filme", "curtas", "documentari"]),
    ("nightlife",   ["festa", "club", "after", "dj set", "noite"]),
    ("comedy",      ["comedia", "stand-up", "stand up", "humor", "improv"]),
    ("performance", ["teatro", "danca", "opera", "performance", "circo", "bailado", "musical"]),
    ("family",      ["familia", "infantil", "crianca", "bebe", "para os mais novos"]),
    ("workshops",   ["workshop", "oficina", "atelier", "curso", "masterclass", "formacao"]),
    ("learning",    ["conferencia", "conversa", "palestra", "ciencia", "literatura", "livro",
                     "debate", "leitura", "visita guiada conferencia", "coloquio"]),
    ("food",        ["gastronomia", "comida", "vinho", "mercado", "degustacao", "brunch"]),
    ("outdoors",    ["festival", "ar livre", "open air", "jardim", "piquenique"]),
    ("tours",       ["visita guiada", "tour", "passeio", "percurso"]),
    ("wellness",    ["bem-estar", "yoga", "meditacao", "mindfulness"]),
    ("music",       ["musica", "concerto", "fado", "jazz", "recital", "sinfonia", "coro", "dj"]),
    ("art",         ["exposi", "arte", "pintura", "fotografia", "galeria", "escultura",
                     "design", "ilustracao", "instalacao"]),
]


def map_topic(*texts, default: str = "art") -> str:
    blob = _fold(" ".join(t for t in texts if t))
    for tid, kws in _TOPIC_KW:
        if any(k in blob for k in kws):
            return tid
    return default


def _strip_html(s) -> str:
    if isinstance(s, list):
        s = s[0] if s else ""
    s = re.sub(r"<[^>]+>", " ", _html.unescape(str(s or "")))
    return re.sub(r"\s+", " ", s).strip()


def _time_from(s: str) -> str | None:
    """First HH:MM in a string like '21:00', '21h', '21h30' -> 'HH:MM'."""
    m = re.search(r"\b(\d{1,2})\s*[:hH]\s*(\d{2})?", str(s or ""))
    if not m:
        return None
    hh = int(m.group(1)); mm = int(m.group(2) or 0)
    if 0 <= hh < 24 and 0 <= mm < 60:
        return f"{hh:02d}:{mm:02d}"
    return None


def _get_json(session, cfg, url, params=None):
    """GET → parsed JSON, or None on any failure (never raises). One retry, since
    these endpoints occasionally time out on a heavy page."""
    timeout = max(cfg["crawl"].get("per_source_timeout_s", 25), 45)
    for attempt in range(2):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            if attempt:
                return None
            time.sleep(1.5)
    return None


def connector_sources() -> list[dict]:
    """Seed-style entries for the connectors, so core.dedupe knows their site,
    canonical name and aggregator status (AgendaLX is an aggregator)."""
    return [{"id": c["id"], "name": c["name"], "website": c["website"],
             "provider": c["id"], "topic": c.get("topic") or "guides",
             "neighbourhood": c.get("neighbourhood"), "zone": c.get("zone")}
            for c in CONNECTORS]


def _src(c: dict, cat_for_topic: dict) -> dict:
    """The seed-style source dict a connector hands to core.make_event."""
    return {
        "id": c["id"], "name": c["name"], "website": c["website"],
        "provider": c["id"], "topic": c.get("topic") or "guides",
        "neighbourhood": c.get("neighbourhood"), "zone": c.get("zone"),
        "categories": [cat_for_topic.get(c.get("topic") or "guides", 1)],
    }


def _resolve_neigh(venue_name, venues_idx, fallback_neigh, fallback_zone):
    """Inherit neighbourhood/zone from a matching seed venue when we know it."""
    if venue_name and venues_idx:
        known = core.resolve_venue(venue_name, venues_idx)
        if known is not None:
            return known.get("neighbourhood") or fallback_neigh, known.get("zone") or fallback_zone
    return fallback_neigh, fallback_zone


# ---------- AgendaLX ----------
def _agendalx_price(item) -> dict:
    cat = item.get("price_cat")
    cat = cat[0] if isinstance(cat, list) and cat else cat
    if cat == "free":
        return {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
    if cat == "value":
        raw = item.get("price_val")
        raw = raw[0] if isinstance(raw, list) and raw else raw
        m = re.search(r's:\d+:"value";s:\d+:"([^"]*)"', str(raw or ""))
        if m:
            return core.parse_price(_html.unescape(m.group(1)))
    return {"is_free": False, "min": None, "currency": "EUR", "text": ""}


def _agendalx(session, cfg, c, source, mon, window_end, venues_idx, delay) -> tuple[list, str]:
    pages = cfg.get("connectors", {}).get("agendalx_max_pages", 12)
    raw, status = [], "ok"
    for page in range(1, pages + 1):
        data = _get_json(session, cfg, c["api"], {"per_page": 100, "page": page})
        if data is None:
            status = "partial" if raw else "failed"
            break
        if not isinstance(data, list) or not data:
            break
        raw.extend(data)
        if len(data) < 100:
            break
        time.sleep(delay)

    out = []
    for it in raw:
        title = _strip_html((it.get("title") or {}).get("rendered"))
        if not title:
            continue
        vs = list((it.get("venue") or {}).values())
        venue = (vs[0].get("name") if vs and isinstance(vs[0], dict) else None)
        if not venue:   # city-wide festivities / walking tours have no fixed venue
            venue = "Lisboa"
        subject = it.get("subject")
        cats = " ".join((it.get("categories_name_list") or {}).keys())
        tags = " ".join((it.get("tags_name_list") or {}).keys())
        topic = map_topic(subject, cats, tags, title, default="art")
        price = _agendalx_price(it)
        img = it.get("featured_media_large") if core._good_img(it.get("featured_media_large") or "") else None
        desc = _strip_html(it.get("description"))
        url = it.get("link")
        tstr = _time_from(it.get("string_times") or "")
        neigh, zone = _resolve_neigh(venue, venues_idx, c.get("neighbourhood"), c.get("zone"))

        # parse occurrences to dates once, dropping anything malformed (a single
        # bad string must not break the whole connector)
        occ = sorted({p[0] for d in (it.get("occurences") or []) if isinstance(d, str)
                      and (p := core.parse_dt(d))})
        sd = core.parse_dt(it.get("StartDate"))
        ld = core.parse_dt(it.get("LastDate"))
        first = occ[0] if occ else (sd[0] if sd else None)
        last = occ[-1] if occ else (ld[0] if ld else first)
        if not first:
            continue
        span = (last - first).days if last else 0

        def emit(start_d, end_d):
            start_iso = f"{start_d.isoformat()}T{tstr}" if tstr else start_d.isoformat()
            ev = core.make_event(
                title=title, source=source, topic=topic, mon=mon, window_end=window_end,
                start_d=start_d, end_d=end_d, has_time=bool(tstr), start_iso=start_iso,
                price=price, url=url, description=desc, language=["pt"],
                categories=source["categories"], venue_name=venue,
                neighbourhood=neigh, zone=zone)
            if ev:
                if img:
                    ev["image"] = core.resolve_url(img, c["website"])
                out.append(ev)

        if span >= 5:  # exhibition / long run -> one ongoing span
            # clamp the stored start to the window so a years-old first occurrence
            # doesn't mis-sort the card; the far end keeps it flagged ongoing
            emit(max(first, mon), last)
        else:           # discrete date(s) -> one card per in-window occurrence
            for d in {dd for dd in (occ or [first]) if mon <= dd <= window_end}:
                emit(d, d)
    return out, status


# ---------- The Events Calendar (Tribe) — CCB ----------
# CCB publishes its school/audience guided-visit booking slots as daily calendar
# entries (one per segment). They are B2B bookings, not public events — drop them.
_TRIBE_SKIP = {"pre-escolar", "pre escolar", "1o ciclo", "2o e 3o ciclos", "secundario",
               "universitario", "primeira infancia", "educacao inclusiva", "creche",
               "visitas guiadas", "visita guiada", "servico educativo"}


def _tribe(session, cfg, c, source, mon, window_end, venues_idx, delay) -> tuple[list, str]:
    pages = cfg.get("connectors", {}).get("ccb_max_pages", 12)
    out, status = [], "ok"
    for page in range(1, pages + 1):
        data = _get_json(session, cfg, c["api"], {
            "per_page": 50, "page": page,
            "start_date": mon.isoformat(), "end_date": window_end.isoformat()})
        if data is None:
            status = "partial" if out else "failed"
            break
        events = (data or {}).get("events") or []
        if not events:
            break
        for it in events:
            title = _strip_html(it.get("title"))
            if not title or _fold(title) in _TRIBE_SKIP:
                continue
            sd = core.parse_dt(it.get("start_date"))
            if not sd:
                continue
            start_d, has_time, start_iso = sd
            if it.get("all_day"):
                has_time, start_iso = False, start_d.isoformat()
            ed = core.parse_dt(it.get("end_date"))
            v = it.get("venue") or {}
            venue = v.get("venue") if isinstance(v, dict) else None
            cat_names = " ".join(x.get("name", "") for x in (it.get("categories") or []) if isinstance(x, dict))
            topic = map_topic(cat_names, title, default=c.get("topic") or "art")
            price = core.scan_price(it.get("cost") or "", allow_free=False) or \
                {"is_free": False, "min": None, "currency": "EUR", "text": ""}
            img = (it.get("image") or {}).get("url") if isinstance(it.get("image"), dict) else None
            neigh, zone = _resolve_neigh(venue, venues_idx, c.get("neighbourhood"), c.get("zone"))
            ev = core.make_event(
                title=title, source=source, topic=topic, mon=mon, window_end=window_end,
                start_d=start_d, end_d=(ed[0] if ed else None), has_time=has_time, start_iso=start_iso,
                price=price, url=it.get("url"), description=_strip_html(it.get("description")),
                language=["pt"], categories=source["categories"],
                venue_name=venue, neighbourhood=neigh, zone=zone)
            if ev:
                if img and core._good_img(img):
                    ev["image"] = core.resolve_url(img, c["website"])
                out.append(ev)
        if len(events) < 50 or not (data or {}).get("next_rest_url"):
            break
        time.sleep(delay)
    return out, status


# ---------- Gulbenkian (WordPress v2 + session/ticket shape) ----------
def _gulbenkian_price(item) -> dict:
    opts = ((item.get("tickets") or {}).get("options") or [])
    raw = opts[0].get("price") if opts and isinstance(opts[0], dict) else ""
    raw = str(raw or "").strip()
    if not raw:
        return {"is_free": False, "min": None, "currency": "EUR", "text": ""}
    if core.FREE_RE.search(raw) or "livre" in _fold(raw) or "gratuit" in _fold(raw):
        return {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
    return core.parse_price(raw)


def _gulbenkian(session, cfg, c, source, mon, window_end, venues_idx, delay) -> tuple[list, str]:
    # No event-date filter on the REST endpoint, so paginate by most-recently
    # published (upcoming events are published recently) and filter by session
    # date locally. A page cap bounds the cost.
    pages = cfg.get("connectors", {}).get("gulbenkian_max_pages", 8)
    # _fields trims the (otherwise huge) ACF/yoast payload so the page loads fast
    # enough not to time out; per_page=50 keeps each response small.
    fields = "id,link,title.rendered,sessions,tickets,acf.lead,acf.performers,yoast_head_json.og_image"
    out, status = [], "ok"
    for page in range(1, pages + 1):
        data = _get_json(session, cfg, c["api"],
                         {"per_page": 50, "page": page, "orderby": "date", "order": "desc",
                          "_fields": fields})
        if data is None:
            status = "partial" if out else "failed"
            break
        if not isinstance(data, list) or not data:
            break
        for it in data:
            title = _strip_html((it.get("title") or {}).get("rendered"))
            if not title:
                continue
            sessions = ((it.get("sessions") or {}).get("sessions") or [])
            if not sessions:
                continue
            price = _gulbenkian_price(it)
            yj = it.get("yoast_head_json") or {}
            og = (yj.get("og_image") or [])
            img = og[0].get("url") if og and isinstance(og[0], dict) else None
            url = it.get("link")
            desc = _strip_html((it.get("acf") or {}).get("lead"))
            perf = (it.get("acf") or {}).get("performers")
            lineup = [p.get("name") for p in perf if isinstance(p, dict) and p.get("name")] if isinstance(perf, list) else []

            for s in sessions:
                typ = s.get("type")
                sd = core.parse_dt(s.get("start"))
                if not sd:
                    continue
                start_d, has_time, start_iso = sd
                ed = core.parse_dt(s.get("end"))
                end_d = ed[0] if ed else None
                if typ == "weekly":   # exhibition with opening hours -> one ongoing span, all-day
                    start_d = max(start_d, mon)   # clamp so an old opening date sorts right
                    has_time, start_iso = False, start_d.isoformat()
                topic = map_topic(title, desc, default=("art" if typ == "weekly" else "music"))
                ev = core.make_event(
                    title=title, source=source, topic=topic, mon=mon, window_end=window_end,
                    start_d=start_d, end_d=end_d, has_time=has_time, start_iso=start_iso,
                    price=price, url=url, description=desc, language=["pt"],
                    categories=source["categories"], venue_name=c["name"],
                    neighbourhood=c.get("neighbourhood"), zone=c.get("zone"))
                if ev:
                    if img and core._good_img(img):
                        ev["image"] = core.resolve_url(img, c["website"])
                    if lineup:
                        ev["lineup"] = lineup
                    out.append(ev)
        if len(data) < 100:
            break
        time.sleep(delay)
    return out, status


_FETCHERS = {"agendalx": _agendalx, "tribe": _tribe, "gulbenkian": _gulbenkian}


def run_all(session, cfg, tax, sources, mon, window_end) -> tuple[list, dict]:
    """Run every enabled connector over [mon, window_end] (a wide horizon when
    called for the pool). Returns (events, status_by_connector)."""
    cat_for_topic = {t["id"]: (t["categories"][0] if t["categories"] else 1) for t in tax["topics"]}
    venues_idx = core.venues_index(sources)
    delay = cfg["crawl"].get("polite_delay_ms", 800) / 1000.0
    events, statuses = [], {}
    for c in CONNECTORS:
        fetch = _FETCHERS.get(c["type"])
        if not fetch:
            statuses[c["id"]] = "failed"
            continue
        source = _src(c, cat_for_topic)
        try:
            evs, st = fetch(session, cfg, c, source, mon, window_end, venues_idx, delay)
        except Exception as e:  # a connector must never break the run
            evs, st = [], "failed"
            print(f"[connector {c['id']}] error: {type(e).__name__}: {e}")
        statuses[c["id"]] = st
        events.extend(evs)
        print(f"[connector {c['id']}] {st}: {len(evs)} events")
        time.sleep(delay)
    return events, statuses
