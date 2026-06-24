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

# a browser User-Agent — some sources (RA GraphQL, Ticketline) refuse the bot UA
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


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
    {
        "id": "ra", "type": "ra", "name": "Resident Advisor",
        "website": "https://ra.co", "topic": "nightlife",
        # the only source for website-less DIY/queer/techno collectives + line-ups
    },
    {
        "id": "bol", "type": "jsonld_listing", "name": "BOL",
        "website": "https://www.bol.pt", "topic": "guides", "default_topic": "art",
        "api": "https://www.bol.pt/Comprar/pesquisa/0-0-11-0-0-0/bilhetes_para_espectaculos_em_lisboa",
        # PT-wide listing (610 events) — filtered to Lisbon by coordinates; best PRICE
    },
    {
        "id": "xceed", "type": "jsonld_detail", "name": "Xceed",
        "website": "https://xceed.me", "topic": "nightlife",
        "listing": "https://xceed.me/en/lisboa/events",
        "slug_re": r"/lisboa/event/[\w-]+/\d+", "detail_base": "https://xceed.me",
    },
    {
        "id": "mato", "type": "jsonld_detail", "name": "Mato",
        "website": "https://ma.to", "topic": "guides",
        "listing": "https://ma.to/events/lisbon",
        "slug_re": r"/event/[\w-]+-\d{1,2}-[a-z]{3}-\d{4}", "detail_base": "https://ma.to",
    },
    {
        "id": "fever", "type": "jsonld_detail", "name": "Fever",
        "website": "https://feverup.com", "topic": "guides", "default_topic": "tours",
        "listing": "https://feverup.com/en/lisbon",
        "slug_re": r"/m/\d+/en", "detail_base": "https://feverup.com",
        # Candlelight, immersive experiences, branded shows — detail pages carry
        # clean schema.org Event JSON-LD (date + geo + offers).
    },
    {
        "id": "dice", "type": "dice", "name": "DICE",
        "website": "https://dice.fm", "topic": "music",
        "api": "https://api.dice.fm/unified_search",
    },
    {
        "id": "meetup", "type": "meetup", "name": "Meetup",
        "website": "https://www.meetup.com", "topic": "learning",
        "api": "https://www.meetup.com/find/pt--lisbon/",
        # community / tech / social / language-exchange — events embedded in the
        # page's Next.js data (no public API). The category culture agendas miss.
    },
    {
        "id": "ticketline", "type": "ticketline", "name": "Ticketline",
        "website": "https://www.ticketline.pt", "topic": "guides",
        "api": "https://www.ticketline.pt/agenda",   # ?district=12 (Lisboa) &page=N
        # the big PT ticketing platform — mainstream concerts (LAV, Campo Pequeno,
        # Coliseu, Altice Arena) that the culture agendas miss. schema.org microdata.
    },
]


# ---------- helpers ----------
def _fold(s: str) -> str:
    """Lowercase, accent-folded — for keyword/topic matching (keeps spaces)."""
    f = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in f if not unicodedata.combining(c)).lower()


def _coord_present(lat, lng) -> bool:
    """True when both lat/lng parse to numbers (a real coordinate to trust),
    regardless of whether they fall inside Lisbon."""
    try:
        float(lat), float(lng)
        return True
    except (TypeError, ValueError):
        return False


def _listing_in_lisbon(lat, lng, locality_folded: str) -> bool:
    """Keep-rule for a PT-wide JSON-LD listing (BOL). A real coordinate is
    authoritative: trust it against the greater-Lisbon bbox and let a clearly
    out-of-area point (e.g. Torres Vedras, lat ~39.09) drop even when its
    addressLocality names the "Lisboa" district. Only when no usable coordinate
    is present do we fall back to the addressLocality string."""
    if _coord_present(lat, lng):
        return core.valid_lisbon_coord(lat, lng)
    return "lisboa" in (locality_folded or "")


_TOPIC_KW = [
    ("film",        ["cinema", "filme", "curtas", "documentari", "ante-estreia", "antestreia",
                     "anteestreia", "longa-metragem"]),
    ("nightlife",   ["festa", "club", "after", "dj set", "noite"]),
    ("comedy",      ["comedia", "stand-up", "stand up", "humor", "improv"]),
    ("performance", ["teatro", "danca", "opera", "performance", "circo", "bailado", "musical", "hamlet"]),
    ("family",      ["familia", "infantil", "crianca", "bebe", "para os mais novos"]),
    ("workshops",   ["workshop", "oficina", "atelier", "curso", "masterclass", "formacao"]),
    ("learning",    ["conferencia", "conversa", "palestra", "ciencia", "literatura", "livro",
                     "debate", "leitura", "coloquio"]),
    ("food",        ["gastronomia", "comida", "vinho", "mercado", "degustacao", "supper", "jantar",
                     "lunch", "dinner", "menu", "michelin", "tasting", "wine"]),
    ("outdoors",    ["festival", "ar livre", "open air", "jardim", "piquenique"]),
    # tourist attractions / experiences / guided visits (a big BOL+Fever bucket)
    ("tours",       ["visita guiada", "visita orientada", "visita livre", "visita", "visit to", "tour",
                     "passeio", "percurso", "oceanari", "aquari", "hop-on", "hop on", "sightseeing",
                     "cruzeiro", "cruise", "skip-the-line", "skip the line", "admission", "ticket to",
                     "pena", "queluz", "national palace", "palacio nacional", "monastery", "mosteiro",
                     "monument", "tower of", "castle", "tuk", "segway", "zoo"]),
    ("wellness",    ["bem-estar", "yoga", "meditacao", "mindfulness"]),
    ("music",       ["musica", "concerto", "fado", "jazz", "recital", "sinfonia", "coro",
                     "tributo", "tribute", "orquestra", "quarteto", "ensemble", "dj set"]),
    # museums / galleries / exhibitions (anything not caught above ending here)
    ("art",         ["exposi", "arte", "pintura", "fotografia", "galeria", "gallery", "escultura",
                     "design", "ilustracao", "instalacao", "museu", "museum", "fundacao", "foundation",
                     "colec", "pinacoteca", "casa fernando pessoa", "casa das historias", "maat"]),
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
    s = s.replace("﻿", "").replace("​", "")   # BOM / zero-width from JSON-LD
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


def _resolve_place(venue_name, venues_geo, venues_idx, fallback_neigh, fallback_zone):
    """Resolve a venue name to (lat, lng, neighbourhood, zone): the venue
    directory (coords + derived neighbourhood) first, then the seed list, then
    the connector's fallback. Coords are None when unknown."""
    lat = lng = None
    neigh, zone = fallback_neigh, fallback_zone
    g = core.venue_geo(venue_name, venues_geo) if venue_name else None
    if g:
        lat, lng = g.get("lat"), g.get("lng")
        neigh = g.get("neighbourhood") or neigh
        zone = g.get("zone") or zone
    if not g and venue_name and venues_idx:
        known = core.resolve_venue(venue_name, venues_idx)
        if known is not None:
            neigh = known.get("neighbourhood") or neigh
            zone = known.get("zone") or zone
    return lat, lng, neigh, zone


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


def _agendalx(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay) -> tuple[list, str]:
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
        lat, lng, neigh, zone = _resolve_place(venue, venues_geo, venues_idx,
                                               c.get("neighbourhood"), c.get("zone"))

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

        def emit(start_d, end_d, ongoing=None):
            start_iso = f"{start_d.isoformat()}T{tstr}" if tstr else start_d.isoformat()
            ev = core.make_event(
                title=title, source=source, topic=topic, mon=mon, window_end=window_end,
                start_d=start_d, end_d=end_d, has_time=bool(tstr), start_iso=start_iso,
                price=price, url=url, description=desc, language=["pt"],
                categories=source["categories"], venue_name=venue,
                neighbourhood=neigh, zone=zone, lat=lat, lng=lng, ongoing=ongoing)
            if ev:
                if img:
                    ev["image"] = core.resolve_url(img, c["website"])
                out.append(ev)

        if span >= 5:  # exhibition / long run -> one ongoing span
            # clamp the stored start to the window so a years-old first occurrence
            # doesn't mis-sort the card, but force ongoing: the clamp would otherwise
            # hide that the run started earlier (= "em curso").
            emit(max(first, mon), last, ongoing=True)
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


def _tribe(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay) -> tuple[list, str]:
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
            lat, lng, neigh, zone = _resolve_place(venue, venues_geo, venues_idx,
                                                   c.get("neighbourhood"), c.get("zone"))
            if lat is None and isinstance(v, dict) and core.valid_lisbon_coord(v.get("geo_lat"), v.get("geo_lng")):
                lat, lng = float(v["geo_lat"]), float(v["geo_lng"])   # Tribe carries venue coords
            ev = core.make_event(
                title=title, source=source, topic=topic, mon=mon, window_end=window_end,
                start_d=start_d, end_d=(ed[0] if ed else None), has_time=has_time, start_iso=start_iso,
                price=price, url=it.get("url"), description=_strip_html(it.get("description")),
                language=["pt"], categories=source["categories"],
                venue_name=venue, neighbourhood=neigh, zone=zone, lat=lat, lng=lng)
            if ev:
                if img and core._good_img(img):
                    ev["image"] = core.resolve_url(img, c["website"])
                out.append(ev)
        if len(events) < 50 or not (data or {}).get("next_rest_url"):
            break
        time.sleep(delay)
    # CCB returns an exhibition as one entry per open day -> collapse same-title
    # multi-day runs into a single ongoing span (a concert keeps its own date).
    return core.collapse_daily_runs(out), status


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


def _gulbenkian(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay) -> tuple[list, str]:
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
            lat, lng, neigh, zone = _resolve_place(c["name"], venues_geo, venues_idx,
                                                   c.get("neighbourhood"), c.get("zone"))

            for s in sessions:
                typ = s.get("type")
                sd = core.parse_dt(s.get("start"))
                if not sd:
                    continue
                start_d, has_time, start_iso = sd
                ed = core.parse_dt(s.get("end"))
                end_d = ed[0] if ed else None
                weekly = typ == "weekly"
                if weekly:   # exhibition with opening hours -> one ongoing span, all-day
                    start_d = max(start_d, mon)   # clamp so an old opening date sorts right
                    has_time, start_iso = False, start_d.isoformat()
                topic = map_topic(title, desc, default=("art" if weekly else "music"))
                ev = core.make_event(
                    title=title, source=source, topic=topic, mon=mon, window_end=window_end,
                    start_d=start_d, end_d=end_d, has_time=has_time, start_iso=start_iso,
                    price=price, url=url, description=desc, language=["pt"],
                    categories=source["categories"], venue_name=c["name"],
                    neighbourhood=neigh, zone=zone, lat=lat, lng=lng,
                    lineup=(lineup or None), ongoing=(True if weekly else None))
                if ev:
                    if img and core._good_img(img):
                        ev["image"] = core.resolve_url(img, c["website"])
                    out.append(ev)
        if len(data) < 50:
            break
        time.sleep(delay)
    return out, status


# ---------- shared schema.org JSON-LD parsing (BOL / Xceed / Mato) ----------
def _lisbon_dt(s):
    """Parse an ISO datetime (UTC 'Z', offset, or naive) -> (date, has_time, iso)
    in Europe/Lisbon wall-clock. has_time is False for a bare date or midnight."""
    s = str(s or "").strip()
    if not s:
        return None, False, None
    try:
        from dateutil import parser as dp
        dt = dp.parse(s)
    except Exception:
        return None, False, None
    if dt.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo
            dt = dt.astimezone(ZoneInfo("Europe/Lisbon")).replace(tzinfo=None)
        except Exception:
            dt = dt.replace(tzinfo=None)
    has_time = (dt.hour != 0 or dt.minute != 0)
    iso = dt.strftime("%Y-%m-%dT%H:%M") if has_time else dt.date().isoformat()
    return dt.date(), has_time, iso


def _jsonld_nodes(html: str) -> list:
    nodes = []
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html or "", re.I | re.S):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        for n in (data if isinstance(data, list) else [data]):
            if isinstance(n, dict) and "Event" in str(n.get("@type", "")):
                nodes.append(n)
    return nodes


def _num(v):
    try:
        n = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 500 else None   # plausibility bound (matches core.scan_price)


def _offers_price(offers) -> dict:
    """Min/max ticket price from schema.org offers. Each offer is its own
    (lo,hi) pair (tiers/fees must not cross-pollute a range); a 0-price offer
    (booking-fee/placeholder) is ignored unless EVERY offer is 0 (=> free)."""
    empty = {"is_free": False, "min": None, "currency": "EUR", "text": ""}
    pairs, saw_zero = [], False
    for o in (offers if isinstance(offers, list) else [offers]):
        if not isinstance(o, dict):
            continue
        lo = _num(o.get("lowPrice")) if o.get("lowPrice") is not None else _num(o.get("price"))
        hi = _num(o.get("highPrice")) if o.get("highPrice") is not None else lo
        if lo is None:
            continue
        if hi is None or hi < lo:
            hi = lo
        if lo == 0 and hi == 0:
            saw_zero = True       # booking-fee / placeholder / free offer
            continue
        pairs.append((lo, hi))
    if pairs:
        lo, hi = min(p[0] for p in pairs), max(p[1] for p in pairs)
        txt = f"€{core._fmt_amount(lo)}" if lo == hi else f"€{core._fmt_amount(lo)}–{core._fmt_amount(hi)}"
        return {"is_free": False, "min": lo, "currency": "EUR", "text": txt}
    if saw_zero:                  # every parseable offer was 0 -> genuinely free
        return {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
    return empty


def _ld_one(v):
    return v[0] if isinstance(v, list) and v else v


def _emit_jsonld(node, c, source, mon, window_end, venues_geo, venues_idx, out):
    """Build an event from one schema.org Event node and append it."""
    title = _strip_html(_ld_one(node.get("name")))
    if not title:
        return
    sd = _lisbon_dt(node.get("startDate"))
    if not sd or not sd[0]:
        return
    start_d, has_time, start_iso = sd
    ed = _lisbon_dt(node.get("endDate"))
    loc = _ld_one(node.get("location")) or {}
    venue = _strip_html(loc.get("name")) if isinstance(loc, dict) else None
    geo = (loc.get("geo") or {}) if isinstance(loc, dict) else {}
    lat = lng = None
    if core.valid_lisbon_coord(geo.get("latitude"), geo.get("longitude")):
        lat, lng = float(geo["latitude"]), float(geo["longitude"])
    _, _, neigh, zone = _resolve_place(venue, venues_geo, venues_idx, c.get("neighbourhood"), c.get("zone"))
    g = core.venue_geo(venue, venues_geo) if (lat is None and venue) else None
    if g and g.get("lat"):
        lat, lng = g["lat"], g["lng"]
    perf = node.get("performer") or node.get("performers")
    lineup = [p.get("name") for p in (perf if isinstance(perf, list) else [perf])
              if isinstance(p, dict) and p.get("name")]
    img = _ld_one(node.get("image"))
    img = img.get("url") if isinstance(img, dict) else img
    # content default: per-connector, never the hidden "guides" aggregator bucket
    default_topic = c.get("default_topic") or (c.get("topic") if c.get("topic") not in (None, "guides") else "art")
    topic = map_topic(title, venue, _strip_html(node.get("description")), default=default_topic)
    ev = core.make_event(
        title=title, source=source, topic=topic, mon=mon, window_end=window_end,
        start_d=start_d, end_d=(ed[0] if ed else None), has_time=has_time, start_iso=start_iso,
        price=_offers_price(node.get("offers")), url=_ld_one(node.get("url")),
        description=_strip_html(node.get("description")), language=["pt"],
        categories=source["categories"], venue_name=venue,
        neighbourhood=neigh, zone=zone, lat=lat, lng=lng, lineup=(lineup or None))
    if ev:
        if img and core._good_img(img):
            ev["image"] = core.resolve_url(img, c["website"])
        out.append(ev)


def _jsonld_listing(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay):
    """One listing page whose JSON-LD already holds every event (BOL). PT-wide,
    so keep only Lisbon events. When a node carries real coordinates, trust the
    greater-Lisbon bbox alone — a coordinate clearly outside the area (e.g. Torres
    Vedras) must drop even if its addressLocality names the "Lisboa" district. Only
    fall back to the addressLocality test when the node has no usable coordinate."""
    data = _get_text(session, cfg, c["api"])
    if data is None:
        return [], "failed"
    out = []
    for node in _jsonld_nodes(data):
        loc = _ld_one(node.get("location")) or {}
        geo = (loc.get("geo") or {}) if isinstance(loc, dict) else {}
        locality = ""
        if isinstance(loc, dict):
            addr = _ld_one(loc.get("address")) or {}
            locality = _fold(addr.get("addressLocality") if isinstance(addr, dict) else addr)
        if _listing_in_lisbon(geo.get("latitude"), geo.get("longitude"), locality):
            _emit_jsonld(node, c, source, mon, window_end, venues_geo, venues_idx, out)
    return out, "ok"


def _jsonld_detail(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay):
    """A listing page of event links, each detail page carrying Event JSON-LD."""
    listing = _get_text(session, cfg, c["listing"])
    if listing is None:
        return [], "failed"
    slugs = list(dict.fromkeys(re.findall(c["slug_re"], listing)))
    out, status = [], ("ok" if slugs else "partial")
    cap = cfg.get("connectors", {}).get("detail_cap", 80)
    for slug in slugs[:cap]:
        html = _get_text(session, cfg, c["detail_base"] + slug)
        if html is None:
            status = "partial"
            continue
        for node in _jsonld_nodes(html):
            _emit_jsonld(node, c, source, mon, window_end, venues_geo, venues_idx, out)
        time.sleep(delay / 2)
    return out, status


# ---------- Resident Advisor (GraphQL) ----------
_RA_QUERY = ("query GET_EVENT_LISTINGS($filters: FilterInputDtoInput, $page: Int, $pageSize: Int) "
             "{ eventListings(filters: $filters, pageSize: $pageSize, page: $page) { data { id event "
             "{ id title date startTime contentUrl images { filename type } venue { name } "
             "artists { name } } } totalResults } }")


def _ra(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay):
    """RA's private GraphQL (area 53 = Lisbon). Browser UA required; fragile, so
    the pool keeps last-good data when it 403s. Dedupe by event id across pages."""
    headers = {"Content-Type": "application/json", "Referer": "https://ra.co/events/pt/lisbon",
               "Origin": "https://ra.co"}
    out, seen, status = [], set(), "ok"
    page, page_size, total = 1, 50, None
    while page <= 12:
        body = {"operationName": "GET_EVENT_LISTINGS",
                "variables": {"filters": {"areas": {"eq": 53},
                              "listingDate": {"gte": mon.isoformat(), "lte": window_end.isoformat()}},
                              "page": page, "pageSize": page_size},
                "query": _RA_QUERY}
        try:
            r = session.post(c["website"] + "/graphql", json=body, headers=headers,
                             timeout=max(cfg["crawl"].get("per_source_timeout_s", 25), 45))
            if r.status_code != 200:
                status = "partial" if out else "failed"
                break
            el = ((r.json() or {}).get("data") or {}).get("eventListings") or {}
        except Exception:
            status = "partial" if out else "failed"
            break
        rows = el.get("data") or []
        total = el.get("totalResults") if total is None else total
        if not rows:
            break
        for row in rows:
            ev = _ra_event(row.get("event") or {}, c, source, mon, window_end, venues_geo, venues_idx, seen)
            if ev:
                out.append(ev)
        if page * page_size >= (total or 0) or len(rows) < page_size:
            break
        page += 1
        time.sleep(delay)
    return out, status


def _ra_event(e, c, source, mon, window_end, venues_geo, venues_idx, seen):
    eid = e.get("id")
    if not eid or eid in seen:
        return None
    seen.add(eid)
    title = _strip_html(e.get("title"))
    when = _lisbon_dt(e.get("startTime") or e.get("date"))
    if not title or not when or not when[0]:
        return None
    start_d, has_time, start_iso = when
    venue = _strip_html((e.get("venue") or {}).get("name"))
    if venue:                       # RA appends the address to the venue name
        venue = re.split(r"\s+[-–]\s+|,", venue)[0].strip()[:80]
    lat, lng, neigh, zone = _resolve_place(venue, venues_geo, venues_idx, c.get("neighbourhood"), c.get("zone"))
    lineup = [a.get("name") for a in (e.get("artists") or []) if isinstance(a, dict) and a.get("name")]
    img = next((i.get("filename") for i in (e.get("images") or [])
                if isinstance(i, dict) and i.get("type") == "FLYERFRONT"), None)
    url = "https://ra.co" + e["contentUrl"] if e.get("contentUrl") else None
    ev = core.make_event(
        title=title, source=source, topic="nightlife", mon=mon, window_end=window_end,
        start_d=start_d, end_d=None, has_time=has_time, start_iso=start_iso,
        price={"is_free": False, "min": None, "currency": "EUR", "text": ""},
        url=url, description="", language=["pt", "en"],
        categories=source["categories"], venue_name=venue,
        neighbourhood=neigh, zone=zone, lat=lat, lng=lng, lineup=(lineup or None))
    if ev and img and core._good_img(img):
        ev["image"] = img
    return ev


# ---------- DICE (unified_search; ~30-event curated cap, supplement) ----------
def _dice(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay):
    seen, out = set(), []
    # vary the centre to widen the curated feed a little, dedupe by perm_name
    for lat0, lng0 in ((38.7223, -9.1393), (38.71, -9.14), (38.74, -9.10)):
        try:
            r = session.post(c["api"], json={"latitude": lat0, "longitude": lng0, "radius": "20km"},
                             timeout=max(cfg["crawl"].get("per_source_timeout_s", 25), 45))
            if r.status_code != 200:
                continue
            secs = (r.json() or {}).get("sections") or []
        except Exception:
            continue
        for s in secs:
            for it in (s.get("items") or []):
                if it.get("type") != "event":
                    continue
                e = it.get("event") or it
                ev = _dice_event(e, c, source, mon, window_end, venues_geo, venues_idx, seen)
                if ev:
                    out.append(ev)
        time.sleep(delay)
    return out, ("ok" if out else "partial")


def _dice_event(e, c, source, mon, window_end, venues_geo, venues_idx, seen):
    perm = e.get("perm_name")
    if not perm or perm in seen:
        return None
    seen.add(perm)
    title = _strip_html(e.get("name"))
    when = _lisbon_dt((e.get("dates") or {}).get("event_start_date"))
    if not title or not when or not when[0]:
        return None
    start_d, has_time, start_iso = when
    ven = (e.get("venues") or [{}])
    venue = _strip_html(ven[0].get("name")) if ven and isinstance(ven[0], dict) else None
    p = e.get("price") or {}
    amt = p.get("amount")
    if amt == 0:
        price = {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
    elif isinstance(amt, (int, float)):
        price = core.parse_price(str(round(amt / 100)))   # whole euros (drop booking-fee cents)
    else:
        price = {"is_free": False, "min": None, "currency": "EUR", "text": ""}
    lineup = [a.get("name") for a in ((e.get("summary_lineup") or {}).get("top_artists") or [])
              if isinstance(a, dict) and a.get("name")]
    img = _ld_one(e.get("images") if isinstance(e.get("images"), list) else (e.get("images") or {}).get("landscape"))
    if isinstance(img, dict):
        img = img.get("url")
    lat, lng, neigh, zone = _resolve_place(venue, venues_geo, venues_idx, c.get("neighbourhood"), c.get("zone"))
    ev = core.make_event(
        title=title, source=source, topic=map_topic(title, venue, default="music"),
        mon=mon, window_end=window_end, start_d=start_d, end_d=None, has_time=has_time,
        start_iso=start_iso, price=price, url=f"https://dice.fm/event/{perm}",
        description="", language=["pt", "en"], categories=source["categories"],
        venue_name=venue, neighbourhood=neigh, zone=zone, lat=lat, lng=lng, lineup=(lineup or None))
    if ev and isinstance(img, str) and core._good_img(img):
        ev["image"] = img
    return ev


# ---------- Meetup (community / tech / social) ----------
# No usable public API (the official one is paid/OAuth), but the city find-page
# server-renders ~50 upcoming events into its Next.js __NEXT_DATA__ blob.
def _meetup(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay) -> tuple[list, str]:
    """Parse Meetup's Lisbon find-page __NEXT_DATA__ for in-person events — the
    community/tech/social/language-exchange category the culture connectors miss.
    No auth, no browser. Returns [] + 'failed' if the markup changes (flagged by
    the shrink/health check, never silent)."""
    html = _get_text(session, cfg, c["api"])
    if not html:
        return [], "failed"
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return [], "failed"
    try:
        data = json.loads(m.group(1))
    except Exception:
        return [], "failed"

    raw = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("__typename") == "Event" and o.get("title") and o.get("dateTime"):
                raw.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)

    out, seen = [], set()
    for e in raw:
        eid = e.get("id")
        if eid in seen:
            continue
        seen.add(eid)
        if e.get("isOnline") or (e.get("eventType") or "PHYSICAL") != "PHYSICAL":
            continue   # in-person only — online meetups aren't "what's on in Lisbon"
        title = _strip_html(e.get("title"))
        start_d, has_time, start_iso = _lisbon_dt(e.get("dateTime"))
        if not title or not start_d:
            continue
        ven = e.get("venue") if isinstance(e.get("venue"), dict) else {}
        if (ven.get("country") or "PT").upper() != "PT":
            continue   # the find page can bleed in nearby-country events
        venue = _strip_html(ven.get("name")) or None
        grp = e.get("group") if isinstance(e.get("group"), dict) else {}
        photo = e.get("featuredEventPhoto") if isinstance(e.get("featuredEventPhoto"), dict) else {}
        img = photo.get("highResUrl")
        lat, lng, neigh, zone = _resolve_place(venue, venues_geo, venues_idx,
                                               c.get("neighbourhood"), c.get("zone"))
        ev = core.make_event(
            title=title, source=source,
            topic=map_topic(title, grp.get("name"), venue, default="learning"),
            mon=mon, window_end=window_end, start_d=start_d, end_d=None, has_time=has_time,
            start_iso=start_iso, price={"is_free": False, "min": None, "currency": "EUR", "text": ""},
            url=e.get("eventUrl"), description="", language=["pt", "en"],
            categories=source["categories"], venue_name=venue, neighbourhood=neigh, zone=zone,
            lat=lat, lng=lng)
        if ev:
            if isinstance(img, str) and core._good_img(img):
                ev["image"] = img
            out.append(ev)
    return out, ("ok" if out else "partial")


# ---------- Ticketline (schema.org microdata agenda, district 12 = Lisboa) ----------
# Lisboa DISTRICT includes far towns; drop the ones outside the greater-Lisbon metro.
_TL_FAR = ("lourinha", "torres vedras", "mafra", "sobral de monte", "alenquer",
           "arruda dos vinhos", "cadaval", "bombarral", "azambuja", "ericeira")


def _tl_blocks(html: str) -> list:
    """Parse the inline Event microdata blocks from a Ticketline agenda page."""
    out = []
    for chunk in re.split(r'itemscope\s+itemtype=["\']https?://schema\.org/(?:Music)?Event["\']', html)[1:]:
        href = re.search(r'href=["\'](/evento/[^"\']+)["\']', chunk)
        date = re.search(r'data-date=["\'](\d{4}-\d{2}-\d{2})["\']', chunk)
        title = re.search(r'class=["\']title["\'][^>]*>([^<]+)<', chunk)
        venue = re.search(r'class=["\']venues["\'][^>]*>([^<]+)<', chunk)
        cat = re.search(r'class=["\']metadata categories["\'][^>]*>([^<]+)<', chunk)
        img = re.search(r'data-src-original=["\']([^"\']+)["\']', chunk)
        if href and date and title:
            out.append({"url": "https://www.ticketline.pt" + href.group(1),
                        "date": date.group(1), "title": _strip_html(title.group(1)),
                        "venue": _strip_html(venue.group(1)) if venue else None,
                        "cat": _strip_html(cat.group(1)) if cat else "",
                        "image": img.group(1) if img else None})
    return out


def _tl_detail(session, cfg, url):
    """Fetch a Ticketline event page for the start TIME and price (microdata +
    text scan). Returns (time_str|None, price_dict|None)."""
    html = _get_text(session, cfg, url)
    if not html:
        return None, None
    t = None
    # the datetime can have content before or after the itemprop attribute
    m = (re.search(r'itemprop=["\']startDate["\'][^>]*content=["\'][^"\']*T(\d{2}:\d{2})', html)
         or re.search(r'content=["\'][^"\']*T(\d{2}:\d{2})[^>]*itemprop=["\']startDate["\']', html)
         or re.search(r'datetime=["\'][^"\']*T(\d{2}:\d{2})[^>]*itemprop=["\']startDate["\']', html))
    if m:
        t = m.group(1)
    price = core.scan_price(core.html_to_text(html, 12000), allow_free=False)
    return t, price


def _ticketline(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay):
    pages = cfg.get("connectors", {}).get("ticketline_max_pages", 30)
    enrich_until = mon + timedelta(days=9)    # detail-fetch (time+price) for the near term
    enriched, enrich_cap = 0, cfg.get("connectors", {}).get("ticketline_detail_cap", 150)
    out, status, seen = [], "ok", set()
    for page in range(1, pages + 1):
        html = _get_text(session, cfg, f"{c['api']}?district=12&page={page}")
        if html is None:
            status = "partial" if out else "failed"
            break
        blocks = _tl_blocks(html)
        if not blocks:
            break
        for b in blocks:
            if b["url"] in seen:
                continue
            seen.add(b["url"])
            venue = b["venue"]
            if venue and any(f in _fold(venue) for f in _TL_FAR):
                continue   # outside greater Lisbon
            sd = core.parse_dt(b["date"])
            if not sd:
                continue
            start_d, has_time, start_iso = sd[0], False, sd[0].isoformat()
            price = {"is_free": False, "min": None, "currency": "EUR", "text": ""}
            # near-term events: one detail fetch for the real time + price
            if start_d <= enrich_until and enriched < enrich_cap:
                enriched += 1
                tm, pr = _tl_detail(session, cfg, b["url"])
                if tm:
                    has_time, start_iso = True, f"{start_d.isoformat()}T{tm}"
                if pr:
                    price = pr
                time.sleep(delay / 2)
            topic = map_topic(b["cat"], b["title"], venue, default="music")
            lat, lng, neigh, zone = _resolve_place(venue, venues_geo, venues_idx,
                                                   c.get("neighbourhood"), c.get("zone"))
            ev = core.make_event(
                title=b["title"], source=source, topic=topic, mon=mon, window_end=window_end,
                start_d=start_d, end_d=None, has_time=has_time, start_iso=start_iso,
                price=price, url=b["url"], description="", language=["pt"],
                categories=source["categories"], venue_name=venue,
                neighbourhood=neigh, zone=zone, lat=lat, lng=lng)
            if ev:
                if b["image"] and core._good_img(b["image"]):
                    ev["image"] = core.resolve_url(b["image"], c["website"])
                out.append(ev)
        time.sleep(delay)
    return out, status


def _get_text(session, cfg, url):
    """GET -> response text (one retry), or None on failure."""
    timeout = max(cfg["crawl"].get("per_source_timeout_s", 25), 45)
    for attempt in range(2):
        try:
            r = session.get(url, timeout=timeout)
            if r.status_code != 200:
                return None
            r.encoding = "utf-8"   # many sites omit charset; requests then guesses latin-1 -> mojibake
            return r.text
        except Exception:
            if attempt:
                return None
            time.sleep(1.5)
    return None


def recover_images(session, cfg, events, delay, near_cutoff: str) -> int:
    """Fill a missing poster by reading the event's OWN page (HTTP, no AI) —
    the same structured recovery the HTML track does in crawl_events.enrich_events,
    but for connector events. Many APIs/listings carry no usable image even though
    the event's detail page has a clean og:image / JSON-LD image.

    Bounded so it never becomes a second crawl: only events that are ongoing or
    start on/before near_cutoff (a far-future pool event gets its image recovered
    in a crawl closer to its date), only those with their own event page (not the
    connector homepage), each page fetched once, and a hard per-run fetch cap.
    Mutates events in place; returns how many posters were recovered."""
    cap = cfg.get("connectors", {}).get("image_recover_cap", 150)
    site_of = {c["id"]: core.site_key(c["website"]) for c in CONNECTORS}
    cache, fetched, recovered = {}, 0, 0
    for e in events:
        if e.get("image") or (e.get("start") or "")[:10] > near_cutoff:
            continue
        u = e.get("url")
        if not u or core.site_key(u) == site_of.get(e.get("source")):
            continue   # homepage fallback — no dedicated event page to read
        if u not in cache:
            if fetched >= cap:
                continue
            fetched += 1
            got = core.fetch(session, u, cfg)
            ok = got and got[0] < 400 and "html" in (got[1] or "")
            cache[u] = core.scrape_event_page(got[2], u) if ok else {}
            time.sleep(delay / 2)
        img = cache[u].get("image")   # scrape_event_page already _good_img-filtered + resolved
        if img:
            e["image"] = img
            recovered += 1
    return recovered


_FETCHERS = {"agendalx": _agendalx, "tribe": _tribe, "gulbenkian": _gulbenkian,
             "jsonld_listing": _jsonld_listing, "jsonld_detail": _jsonld_detail,
             "ra": _ra, "dice": _dice, "ticketline": _ticketline, "meetup": _meetup}


def run_all(session, cfg, tax, sources, mon, window_end) -> tuple[list, dict]:
    """Run every enabled connector over [mon, window_end] (a wide horizon when
    called for the pool). Returns (events, status_by_connector)."""
    cat_for_topic = {t["id"]: (t["categories"][0] if t["categories"] else 1) for t in tax["topics"]}
    venues_idx = core.venues_index(sources)
    venues_geo = core.load_venues()
    # several sites (RA, Ticketline) block the bot UA — present a browser one for
    # the connector session (it only hits public APIs/listings, never the long tail)
    session.headers["User-Agent"] = _UA
    delay = cfg["crawl"].get("polite_delay_ms", 800) / 1000.0
    events, statuses = [], {}
    for c in CONNECTORS:
        fetch = _FETCHERS.get(c["type"])
        if not fetch:
            statuses[c["id"]] = "failed"
            continue
        source = _src(c, cat_for_topic)

        def _run():
            try:
                return fetch(session, cfg, c, source, mon, window_end, venues_idx, venues_geo, delay)
            except Exception as e:  # a connector must never break the run
                print(f"[connector {c['id']}] error: {type(e).__name__}: {e}")
                return [], "failed"

        evs, st = _run()
        # one retry after a pause for a transient empty result (a momentary block /
        # rate-limit from the datacenter IP — these connectors normally succeed, so a
        # second attempt usually recovers them). Connectors are free; the cost is a wait.
        if not evs and st != "ok":
            time.sleep(8)
            evs2, st2 = _run()
            if evs2:
                evs, st = evs2, st2
                print(f"[connector {c['id']}] recovered on retry")
        statuses[c["id"]] = st
        events.extend(evs)
        print(f"[connector {c['id']}] {st}: {len(evs)} events")
        time.sleep(delay)
    return events, statuses
