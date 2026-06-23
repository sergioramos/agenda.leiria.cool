"""
Shared helpers for the Pregoeiro crawler: config + data IO, the week window,
HTTP fetching, ICS feed discovery/parsing, price detection,
date→day mapping, and event normalisation/dedup.

Pure-stdlib where possible; third-party deps are imported lazily so the
no-AI / feeds dry-run still works if some optional libs are missing.
"""
from __future__ import annotations
import difflib
import hashlib
import json
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml
import requests

ROOT = Path(__file__).resolve().parents[1]
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# ---------- config + data ----------
def load_config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

def load_taxonomy() -> dict:
    return json.loads((ROOT / "sources" / "taxonomy.json").read_text(encoding="utf-8"))

def load_sources() -> dict:
    return json.loads((ROOT / "sources" / "sources.json").read_text(encoding="utf-8"))

def save_sources(payload: dict) -> None:
    (ROOT / "sources" / "sources.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def topic_ids(tax: dict) -> set[str]:
    return {t["id"] for t in tax["topics"]}

# ---------- persistent pool ----------
# The structured connectors fetch a WIDE horizon into this pool once per run;
# the weekly publish just filters it. It survives a failed run (last-good data
# stays) and lets multi-day/advance events accumulate. Keyed by event id.
POOL_PATH = ROOT / "docs" / "data" / "pool.json"


def load_pool() -> dict:
    if POOL_PATH.exists():
        try:
            d = json.loads(POOL_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("events"), dict):
                return d
        except Exception:
            pass
    return {"events": {}, "updated_at": None}


def save_pool(pool: dict) -> None:
    POOL_PATH.parent.mkdir(parents=True, exist_ok=True)
    POOL_PATH.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")


def pool_upsert(pool: dict, events: list[dict], connector: str, stamp: str) -> None:
    """Insert/refresh this run's connector events. Fresh data wins; first-seen
    is preserved. A connector that returned nothing this run leaves its earlier
    entries untouched (they expire by date), so a failed fetch never wipes them."""
    store = pool.setdefault("events", {})
    for e in events:
        pid = e.get("id")
        if not pid:
            continue
        prev = store.get(pid) or {}
        store[pid] = {**e, "_connector": connector,
                      "_first_seen": prev.get("_first_seen", stamp), "_last_seen": stamp}


def pool_expire(pool: dict, today: date, grace_days: int = 2) -> int:
    """Drop pooled events whose last date is more than grace_days in the past.
    Returns how many were removed."""
    cutoff = (today - timedelta(days=grace_days)).isoformat()
    store = pool.get("events", {})
    keep = {pid: e for pid, e in store.items()
            if ((e.get("end") or e.get("start") or "")[:10] or "9999") >= cutoff}
    removed = len(store) - len(keep)
    pool["events"] = keep
    return removed


def pool_events(pool: dict) -> list[dict]:
    """Pooled events as plain event dicts (internal _-prefixed keys stripped)."""
    return [{k: v for k, v in e.items() if not k.startswith("_")}
            for e in pool.get("events", {}).values()]


# ---------- connector health (silent-shrink detection) ----------
# A connector that returns ~half its usual count is the real failure mode the
# 0-event guard misses (a JSON shape drift, a plugin update). We keep a rolling
# median of each connector's count and flag a run that falls below 50% of it.
CONNECTOR_STATE_PATH = ROOT / "docs" / "data" / "connector_state.json"


def load_connector_state() -> dict:
    if CONNECTOR_STATE_PATH.exists():
        try:
            d = json.loads(CONNECTOR_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    return {}


def save_connector_state(state: dict) -> None:
    CONNECTOR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONNECTOR_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def update_connector_health(counts: dict, statuses: dict, stamp: str) -> dict:
    """Update the rolling per-connector history and downgrade an 'ok' status to
    'shrunk' when its count drops below 50% of the rolling median (>=3 samples,
    median>=5). Persists the state and returns the (possibly downgraded) statuses."""
    import statistics
    state = load_connector_state()
    for conn, st in list(statuses.items()):
        rec = state.setdefault(conn, {"counts": []})
        hist = rec.get("counts", [])
        cnt = int(counts.get(conn, 0))
        med = statistics.median(hist) if hist else 0
        if st == "ok" and len(hist) >= 3 and med >= 5 and cnt < 0.5 * med:
            statuses[conn] = "shrunk"
        # only healthy, non-empty runs feed the median (a failure must not poison it)
        if st == "ok" and cnt > 0:
            rec["counts"] = (hist + [cnt])[-8:]
        rec.update(last_count=cnt, last_status=statuses[conn],
                   median=med, updated_at=stamp)
    save_connector_state(state)
    return statuses

# ---------- spend tracking ----------
def month_ai_spend(today: date) -> float:
    """Sum of ai_cost_usd across committed week files generated this calendar
    month (samples excluded). Used to enforce ai.max_month_cost_usd."""
    spent = 0.0
    weeks_dir = ROOT / "docs" / "data" / "weeks"
    if not weeks_dir.exists():
        return 0.0
    prefix = today.strftime("%Y-%m")
    for p in weeks_dir.glob("*.json"):
        if p.name == "index.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("is_sample"):
            continue
        if str(d.get("generated_at", ""))[:7] == prefix:
            spent += float((d.get("meta") or {}).get("ai_cost_usd", 0) or 0)
    return spent


def effective_run_cap(cfg: dict, today: date) -> tuple[float, float]:
    """(cap_for_this_run, month_spent). Run cap shrinks to whatever is left of
    the monthly ceiling; 0 means the month's budget is exhausted."""
    run_cap = float(cfg["ai"].get("max_run_cost_usd", 2.0))
    month_cap = cfg["ai"].get("max_month_cost_usd")
    spent = month_ai_spend(today)
    if month_cap is None:
        return run_cap, spent
    return max(0.0, min(run_cap, float(month_cap) - spent)), spent


# ---------- week window ----------
def target_monday(today: date) -> date:
    """Monday of the relevant week. Sunday rolls forward to next week (the
    Sunday crawl publishes the upcoming Mon–Sun); Mon–Sat use the current week."""
    mon = today - timedelta(days=today.weekday())
    if today.weekday() == 6:  # Sunday
        mon += timedelta(days=7)
    return mon

def week_window(today: date, lookahead_days: int = 7):
    mon = target_monday(today)
    display_sun = mon + timedelta(days=6)
    window_end = mon + timedelta(days=max(lookahead_days, 7) - 1)
    return mon, display_sun, window_end

# ---------- dates ----------
def parse_dt(value):
    """Best-effort parse of a date/datetime/ISO string → (date, has_time, iso)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date(), True, value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value, False, value.isoformat()
    s = str(value).strip()
    if not s:
        return None
    try:
        from dateutil import parser as dparser
        dt = dparser.parse(s, dayfirst=False, fuzzy=True)
        has_time = bool(re.search(r"\d{1,2}:\d{2}", s))
        return dt.date(), has_time, (dt.isoformat(timespec="minutes") if has_time else dt.date().isoformat())
    except Exception:
        return None

def days_in_window(start_d: date, end_d: date | None, mon: date, window_end: date):
    """Day codes the event covers within [mon, window_end], plus an 'ongoing' flag
    (started before the window). Returns (days, ongoing)."""
    end_d = end_d or start_d
    if end_d < start_d:
        end_d = start_d
    lo = max(start_d, mon)
    hi = min(end_d, window_end)
    if hi < lo:
        return [], False
    out, d = [], lo
    while d <= hi and d <= mon + timedelta(days=6):  # day chips only span the displayed Mon–Sun
        out.append(DAYS[d.weekday()])
        d += timedelta(days=1)
    ongoing = start_d < mon or (end_d - start_d).days >= 5
    return (out or [DAYS[lo.weekday()]]), ongoing

def overlaps_window(start_d: date, end_d: date | None, mon: date, window_end: date) -> bool:
    end_d = end_d or start_d
    return start_d <= window_end and end_d >= mon


def reframe_window(events: list[dict], mon: date, window_end: date) -> list[dict]:
    """Recompute days/ongoing for the display window from each event's absolute
    start/end, dropping any that no longer overlap. Pooled (wide-horizon) events
    carry day chips computed for a different window, so they are reframed here
    before publishing a specific week."""
    out = []
    for e in events:
        sd = parse_dt(e.get("start"))
        if not sd:
            continue
        ed = parse_dt(e.get("end")) if e.get("end") else None
        start_d, end_d = sd[0], (ed[0] if ed else None)
        if not overlaps_window(start_d, end_d, mon, window_end):
            continue
        days, ongoing = days_in_window(start_d, end_d, mon, window_end)
        if not days:
            continue
        out.append({**e, "days": days, "ongoing": ongoing})
    return out

# ---------- http ----------
def make_session(cfg: dict) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": cfg["crawl"].get("user_agent", "PregoeiroBot/1.0"),
                      "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.8"})
    return s

def fetch(session: requests.Session, url: str, cfg: dict):
    """GET with retries. Returns (status_code, content_type, text) or None on failure."""
    timeout = cfg["crawl"].get("per_source_timeout_s", 25)
    retries = cfg["crawl"].get("retries", 2)
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=timeout, allow_redirects=True)
            ct = r.headers.get("content-type", "")
            return r.status_code, ct, r.text
        except requests.RequestException:
            if attempt == retries:
                return None
            time.sleep(0.6 * (attempt + 1))
    return None

def html_to_text(html: str, max_chars: int, base_url: str | None = None,
                 keep_links: bool = False, link_sink: set | None = None) -> str:
    """Page text for the AI. With keep_links, anchor URLs are appended inline as
    "label [https://…]" so the model can return each event's own page URL —
    plain text extraction would otherwise destroy every href. Kept URLs are
    added to link_sink so the extractor can verify what the model returns."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
            tag.decompose()
        if keep_links and base_url:
            page = site_key(base_url)
            seen, kept = set(), 0
            for a in soup.find_all("a", href=True):
                if kept >= 90:  # bound the token cost on link-farm pages
                    break
                try:  # one malformed href must not cost the page its links
                    label = a.get_text(" ", strip=True)
                    href = urljoin(base_url, a["href"].strip()).split("#", 1)[0]
                    if len(label) < 4 or len(href) > 160 or not href.lower().startswith(("http://", "https://")):
                        continue
                    if href in seen or site_key(href) == page:
                        continue
                    seen.add(href)
                    kept += 1
                    a.append(soup.new_string(f" [{href}]"))
                except ValueError:
                    continue
            if link_sink is not None:
                link_sink.update(seen)
        text = soup.get_text(" ", strip=True)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]

# ---------- feeds ----------
def discover_feeds(html: str, base_url: str) -> list[str]:
    feeds = []
    try:
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin
        soup = BeautifulSoup(html, "lxml")
        for link in soup.find_all("link", rel=lambda v: v and "alternate" in v):
            t = (link.get("type") or "").lower()
            href = link.get("href")
            if href and ("rss" in t or "atom" in t or "xml" in t):
                feeds.append(urljoin(base_url, href))
        for a in soup.find_all("a", href=True):
            if a["href"].lower().endswith(".ics"):
                feeds.append(urljoin(base_url, a["href"]))
    except Exception:
        pass
    return list(dict.fromkeys(feeds))

def parse_ics(text: str):
    """Parse an ICS string → list of raw events."""
    out = []
    try:
        from icalendar import Calendar
        cal = Calendar.from_ical(text)
        for comp in cal.walk("VEVENT"):
            start = comp.get("dtstart")
            out.append({
                "title": str(comp.get("summary") or "").strip(),
                "start": start.dt if start else None,
                "end": comp.get("dtend").dt if comp.get("dtend") else None,
                "url": str(comp.get("url") or "") or None,
                "desc": str(comp.get("description") or "").strip(),
            })
    except Exception:
        pass
    return out

# ---------- price + tagging ----------
FREE_RE = re.compile(r"\b(gr[áa]tis|gratuito|entrada\s+livre|free\s+entry|free\b|sem\s+custo|entrada\s+gratuita)\b", re.I)
PRICE_RE = re.compile(r"(?:€|eur\s?)\s?(\d{1,3}(?:[.,]\d{2})?)", re.I)

def detect_price(text: str) -> dict:
    """Scan free-text (feed descriptions) for a price. Conservative: only a
    €/eur-prefixed number counts, so stray years/counts aren't read as prices."""
    t = text or ""
    if FREE_RE.search(t):
        return {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
    m = PRICE_RE.search(t)
    if m:
        amt = float(m.group(1).replace(",", "."))
        return {"is_free": False, "min": amt, "currency": "EUR", "text": f"€{m.group(1)}"}
    return {"is_free": False, "min": None, "currency": "EUR", "text": ""}


def parse_price(text: str) -> dict:
    """Normalise the model's price_text field, which is KNOWN to be a price, so
    a bare '12' or a '12-20' range is accepted (detect_price needs a € prefix).
    Renders '€12' or '€12–20'."""
    t = (text or "").strip()
    out = {"is_free": False, "min": None, "currency": "EUR", "text": ""}
    if not t:
        return out
    if FREE_RE.search(t):
        return {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
    nums = re.findall(r"\d{1,4}(?:[.,]\d{1,2})?", t)
    if not nums:
        return out
    out["min"] = float(nums[0].replace(",", "."))
    if len(nums) >= 2 and re.search(r"[-–—/]|\ba\b|\bàs?\b|\bate?\b|\baté\b", t, re.I):
        out["text"] = f"€{nums[0]}–{nums[1]}"
    else:
        out["text"] = f"€{nums[0]}"
    return out


# euro amount with the sign on EITHER side ("€28", "28€", "28 eur", "28 euros")
_MONEY_RE = re.compile(
    r"(?:€\s?(\d{1,4}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d{1,4}(?:[.,]\d{1,2})?))"
    r"|(?:(\d{1,4}(?:[.\s]\d{3})*(?:,\d{1,2})?|\d{1,4}(?:[.,]\d{1,2})?)\s?(?:€|eur(?:os?)?\b))", re.I)


def _money_to_float(raw: str):
    s = raw.strip().replace(" ", "")
    if "," in s:               # PT decimal comma; dots are thousands
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") == 1 and len(s.split(".")[1]) == 3:
        s = s.replace(".", "")  # "1.250" = thousands, not 1.25
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_amount(v: float) -> str:
    return str(int(v)) if v == int(v) else f"{v:.2f}".replace(".", ",")


def scan_price(text: str, allow_free: bool = True) -> dict | None:
    """Find a ticket price anywhere in free text (e.g. a whole event page),
    with the € on either side and ranges ('28€ a 40€' → €28–40). Conservative:
    needs the € / 'eur' token, ignores implausible amounts. None if nothing.
    allow_free=False ignores 'grátis'/'free' keywords — on a full page they are
    too often unrelated (newsletter, shipping), so only numbers/JSON-LD count."""
    t = text or ""
    if allow_free and FREE_RE.search(t):
        return {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
    vals = []
    for m in _MONEY_RE.finditer(t):
        v = _money_to_float(m.group(1) or m.group(2) or "")
        if v is not None and 0 < v <= 500:
            vals.append(round(v, 2))
    if not vals:
        return None
    lo, hi = min(vals), max(vals)
    text_out = f"€{_fmt_amount(lo)}" if lo == hi else f"€{_fmt_amount(lo)}–{_fmt_amount(hi)}"
    return {"is_free": lo == 0, "min": lo, "currency": "EUR", "text": "Grátis" if lo == 0 else text_out}


# ---------- event-page enrichment (read the event's OWN page, no AI) ----------
# Match each chrome word only as a whole path/filename token (bounded by a non
# -alphanumeric char or the string ends), not as a coincidental substring — so a
# real poster like ".../em-dialogo-com-..." (contains "logo"), ".../catalogo-..."
# or Xceed's "/events/banners/..." (the trailing "s" breaks the boundary) is kept,
# while "logo.png" / "banner-top.jpg" / "favicon.ico" are still rejected. "default"
# is matched only as a filename token (default[-_.]) so the very common Drupal
# media path "/sites/default/files/..." is NOT mistaken for a default image.
_LOGO_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:logo|favicon|sprite|placeholder|header|icons?|banner)(?:[^a-z0-9]|$)"
    r"|(?:^|[^a-z0-9])default[-_.]", re.I)
_OG_IMG_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image(?::secure_url)?["\'][^>]*content=["\']([^"\']+)'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']og:image', re.I)


def og_image(html: str) -> str:
    """The page's og:image URL (raw), or '' — used to learn a site's default
    image so per-event scraping can reject it as a logo."""
    m = _OG_IMG_RE.search(html or "")
    return (m.group(1) or m.group(2)) if m else ""


def _good_img(src: str) -> bool:
    """A plausible event poster: not a logo/banner, not an SVG/data-URI/gif."""
    s = (src or "").strip()
    return bool(s) and not s.startswith("data:") and not _LOGO_RE.search(s) \
        and not re.search(r"\.(svg|gif)(\?|$)", s, re.I)


def drop_shared_images(events: list[dict]) -> None:
    """An image reused by events with different titles is a venue logo / default
    banner, not a poster — clear it so the site shows the topic emoji instead."""
    titles: dict = {}
    for e in events:
        if e.get("image"):
            titles.setdefault(e["image"], set()).add(_nt(e.get("title"))[:30])
    for e in events:
        if e.get("image") and len(titles[e["image"]]) >= 2:
            e["image"] = None


def _iter_jsonld(node):
    if isinstance(node, list):
        for n in node:
            yield from _iter_jsonld(n)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _iter_jsonld(node["@graph"])
        yield node


def _jsonld_price(offers) -> dict | None:
    for o in (offers if isinstance(offers, list) else [offers]):
        if not isinstance(o, dict):
            continue
        lo = o.get("lowPrice") or o.get("price")
        hi = o.get("highPrice") or o.get("price")
        try:
            lo, hi = float(str(lo).replace(",", ".")), float(str(hi).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if hi < lo:
            lo, hi = hi, lo
        if lo == 0 and hi == 0:
            return {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
        txt = f"€{_fmt_amount(lo)}" if lo == hi else f"€{_fmt_amount(lo)}–{_fmt_amount(hi)}"
        return {"is_free": False, "min": lo, "currency": "EUR", "text": txt}
    return None


def parse_jsonld_event(html: str) -> dict:
    """Pull image / price / startDate / description from a schema.org Event in
    the page's JSON-LD. Best-effort; never raises."""
    out: dict = {}
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         html or "", re.I | re.S):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        for node in _iter_jsonld(data):
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if not any(isinstance(x, str) and x.lower().endswith("event") for x in types):
                continue
            if "image" not in out:
                img = node.get("image")
                if isinstance(img, list):
                    img = img[0] if img else None
                if isinstance(img, dict):
                    img = img.get("url")
                if isinstance(img, str):
                    out["image"] = img
            if "price" not in out:
                p = _jsonld_price(node.get("offers"))
                if p:
                    out["price"] = p
            if "start" not in out and isinstance(node.get("startDate"), str):
                out["start"] = node["startDate"]
            if "description" not in out and isinstance(node.get("description"), str):
                out["description"] = node["description"]
            if "venue" not in out:
                loc = node.get("location")
                if isinstance(loc, list):
                    loc = loc[0] if loc else None
                vn = loc.get("name") if isinstance(loc, dict) else None
                if isinstance(vn, list):
                    vn = vn[0] if vn else None
                if isinstance(vn, str):
                    vn = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", vn)).strip()
                    if vn:
                        out["venue"] = vn
    return out


def _canonical_img(img_url: str | None, page_url: str | None) -> str | None:
    """Some Rails/ActiveStorage sites behind AWS Elastic Beanstalk leak their
    internal *.elasticbeanstalk.com origin into og:image; that host's TLS cert is
    rejected by browsers, so the poster shows broken (e.g. Visit Lisboa). The same
    path served from the site's own public host works, so swap the origin host for
    the page's host."""
    if not img_url:
        return img_url
    try:
        ip = urlparse(img_url)
        if ip.netloc.lower().endswith(".elasticbeanstalk.com"):
            ph = urlparse(page_url or "").netloc
            if ph and not ph.lower().endswith(".elasticbeanstalk.com"):
                return ip._replace(netloc=ph).geturl()
    except ValueError:
        pass
    return img_url


def scrape_event_page(html: str, url: str, default_img: str = "") -> dict:
    """Read an event's own page (no AI): JSON-LD first, then og:image + a text
    price scan. Returns any of {image, price, start_time, description}.
    default_img = the source's homepage/listing og:image, rejected here because
    it's the venue's logo/default, not this event's poster."""
    out: dict = {}
    df = (default_img or "").strip()
    ld = parse_jsonld_event(html or "")
    for cand in (ld.get("image"), og_image(html)):
        if _good_img(cand) and cand.strip() != df:
            out["image"] = _canonical_img(resolve_url(cand, url), url)
            break
    if ld.get("price"):
        out["price"] = ld["price"]
    else:
        # numbers only — a bare "grátis"/"free" anywhere on the page is unreliable
        p = scan_price(html_to_text(html or "", 14000), allow_free=False)
        if p:
            out["price"] = p
    sd = ld.get("start")
    if sd:
        mt = re.search(r"T(\d{2}:\d{2})", sd)
        if mt:
            out["start_time"] = mt.group(1)
    if ld.get("description"):
        out["description"] = clean_description(ld["description"], "", "")
    if ld.get("venue"):
        out["venue"] = ld["venue"]
    return {k: v for k, v in out.items() if v}


def eventon_events(html: str) -> list[dict]:
    """Per-event {name, url, image} from EventON's hidden schema.org microdata
    blocks. EventON's per-page JSON-LD is unreliable (every page repeats all
    events, with malformed dates), but each event carries clean itemprop
    name/url/image on its listing block — the only trustworthy per-event image
    on these sites (e.g. Hot Clube de Portugal)."""
    out, seen = [], set()
    for blk in re.findall(r'<div class=["\']evo_event_schema["\'].*?</div>', html or "", re.S):
        nm = re.search(r"itemprop=['\"]name['\"][^>]*>([^<]+)<", blk)
        ur = re.search(r"itemprop=['\"]url['\"][^>]*href=['\"]([^'\"]+)", blk)
        im = re.search(r"itemprop=['\"]image['\"][^>]*content=['\"]([^'\"]+)", blk)
        if not (nm and ur) or ur.group(1) in seen:
            continue
        seen.add(ur.group(1))
        img = im.group(1) if im else None
        if img and not (re.search(r"\.(jpe?g|png|webp)(\?|$)", img, re.I) and _good_img(img)):
            img = None   # guard against a non-image content attr
        out.append({"name": re.sub(r"\s+", " ", nm.group(1)).strip(), "url": ur.group(1), "image": img})
    return out


def canonical_venue(name: str, venues_geo: dict | None, venues_idx: dict | None) -> dict | None:
    """Resolve a venue name to its canonical form via the venue directory (coords +
    neighbourhood), then the seed list. Returns {venue, neighbourhood, zone, lat,
    lng} — falling back to the given name when it matches nothing known, so a real
    venue read off an event page still replaces a source-name placeholder. None for
    an empty/junk name."""
    if not name or looks_like_date(name):
        return None
    g = venue_geo(name, venues_geo or {})
    if g:
        return {"venue": g.get("name") or name, "neighbourhood": g.get("neighbourhood"),
                "zone": g.get("zone"), "lat": g.get("lat"), "lng": g.get("lng")}
    k = resolve_venue(name, venues_idx or {})
    if k:
        return {"venue": k.get("name") or name, "neighbourhood": k.get("neighbourhood"),
                "zone": k.get("zone"), "lat": None, "lng": None}
    return {"venue": name, "neighbourhood": None, "zone": None, "lat": None, "lng": None}


def _strip_subroom(name: str) -> str:
    """Drop a trailing sub-room / annotation a source tacks on: a '| Sala 1'
    segment or a final '(...)'. Dashes are left alone (real venue names contain
    them, e.g. 'MAAT - Museu de Arte...')."""
    s = re.sub(r"\s*\|\s*.*$", "", name or "")
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def canonicalize_venues(events: list[dict], venues_geo: dict | None, venues_idx: dict | None) -> int:
    """Unify venue-name variants to the directory's canonical spelling using an
    EXACT normalized match only (optionally after dropping a sub-room/parenthetical
    suffix). No fuzzy/containment matching, so a loose match can never relabel a
    venue as a different place (e.g. 'Lisboa' must never become '@esnlisboa').
    Backfills neighbourhood/zone/coords from the matched entry when the event lacks
    them. Returns how many venue names were changed."""
    vg, vi = (venues_geo or {}), (venues_idx or {})
    changed = 0
    for e in events:
        name = e.get("venue") or ""
        nk = _nt(name)
        if len(nk) < 4:
            continue
        cands = [name]
        stripped = _strip_subroom(name)
        if stripped and _nt(stripped) != nk:
            cands.append(stripped)
        for cand in cands:
            hit = vg.get(_nt(cand)) or vi.get(_nt(cand))
            if not hit:
                continue
            canon = hit.get("name") or name
            if _nt(canon) == nk:        # stored name is already the canonical one
                break
            e["venue"] = canon
            if hit.get("neighbourhood") and not e.get("neighbourhood"):
                e["neighbourhood"], e["zone"] = hit["neighbourhood"], hit.get("zone")
            if hit.get("lat") is not None and not valid_lisbon_coord(e.get("lat"), e.get("lng")):
                e["lat"], e["lng"] = hit.get("lat"), hit.get("lng")
            changed += 1
            break
    return changed


# ---------- normalisation ----------
def _nt(s: str) -> str:
    """letters+digits only, accent-folded and lowercased — the comparison form
    of a title/name (models and agenda pages often drop PT accents)."""
    folded = unicodedata.normalize("NFKD", str(s or ""))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def site_key(url: str) -> str:
    """host+path of a URL (www/trailing-slash stripped) — identifies 'the same
    page' across source entries that list it under different names."""
    if not url:
        return ""
    raw = str(url).strip().lower()
    try:
        p = urlparse(raw)
    except ValueError:  # e.g. unbalanced [ in the authority — keep it distinct, never crash
        return raw
    host = p.netloc[4:] if p.netloc.startswith("www.") else p.netloc
    return host + p.path.rstrip("/")


# words kept lowercase when re-casing an ALL-CAPS title (unless first)
_STOP = {"a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na",
         "nos", "nas", "com", "para", "por", "ao", "à", "aos", "às", "um", "uma",
         "que", "se", "sob", "sobre", "entre", "até", "the", "of", "and", "at", "in", "on", "to"}
# acronyms that must stay upper-case (vowel-less words are detected automatically)
_ACRO = {"DJ", "VJ", "ZDB", "CCB", "CAM", "MAAT", "FIL", "LX", "AVNL", "VR", "EDP",
         "NOS", "RTP", "TBA", "TBC", "UV", "3D", "B2B", "EP", "LP", "UK", "USA",
         "NYC", "EUA", "IST", "FCSH", "ISCTE", "MNAA", "MNAC", "TNDM", "TNSC", "FMM", "MIL"}
# strict roman-numeral grammar: matches IV/XIV/III, rejects words like CIVIL/VIL
_ROMAN_RE = re.compile(r"^(?=[IVXLC])(C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_VOWEL_RE = re.compile(r"[aeiouáéíóúâêôãõà]", re.I)


def _decap_word(word: str, first: bool) -> str:
    out = []
    for part in re.split(r"(-)", word):  # K-POP -> K, -, POP
        if part == "-" or not part:
            out.append(part)
            continue
        if part.upper() in _ACRO or _ROMAN_RE.match(part) or \
                (not _VOWEL_RE.search(part) and any(c.isalpha() for c in part)):
            out.append(part.upper())
        else:
            low = part.lower()
            out.append(low if (low in _STOP and not first) else low.capitalize())
    return "".join(out)


def clean_title(title: str, venue: str = "") -> str:
    """Readable, consistent event titles: collapse whitespace, turn stray |/_
    separators into dashes, drop a trailing repeat of the venue name, and
    re-case ALL-CAPS titles (PT stopwords lowered, acronyms preserved)."""
    t = re.sub(r"\s+", " ", str(title or "")).strip().strip("«»\"“”'’").strip()
    t = re.sub(r"\s*[|_]+\s*", " – ", t)
    t = re.sub(r"\s*[–—]\s*", " – ", t)
    t = t.strip("–- ")
    if venue:
        m = re.match(r"^(.{4,}?)\s+[–—-]\s+([^–—-]+)$", t)
        if m and _nt(m.group(2)) == _nt(venue):
            t = m.group(1)
    letters = [c for c in t if c.isalpha()]
    if len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.85:
        t = " ".join(_decap_word(w, i == 0) for i, w in enumerate(t.split(" ")))
    return t[:160].strip()


def clean_description(desc: str, title: str = "", venue: str = "") -> str:
    """Readable one-to-two-sentence descriptions: strip html/whitespace, drop a
    leading repeat of the title and a trailing '— venue', sentence-case the
    first letter, end with a period."""
    d = re.sub(r"<[^>]+>", " ", str(desc or ""))
    d = re.sub(r"\s+", " ", d).strip().strip("«»\"“”")
    if title:
        m = re.match(r"^(.{0,160}?)\s*[–—:|-]\s+(.{12,})$", d)
        if m and _nt(m.group(1)) == _nt(title):
            d = m.group(2)
    if venue:
        m = re.match(r"^(.{12,}?)\s*[–—|-]\s*([^–—|-]{0,80})\.?$", d)
        if m and _nt(m.group(2)) == _nt(venue):
            d = m.group(1)
    if _nt(d) == _nt(title) or _nt(d) == _nt(venue):
        return ""
    d = d.strip()[:280].strip()
    if d:
        d = d[0].upper() + d[1:]
        if d[-1].isalnum():
            d += "."
    return d


def venues_index(sources: list[dict]) -> dict:
    """normalized-name -> source, for mapping AI-extracted venue names back to
    the seed list (canonical name + neighbourhood/zone)."""
    return {_nt(s["name"]): s for s in sources if s.get("name")}


def resolve_venue(name: str, idx: dict) -> dict | None:
    """Match an extracted venue name to a known source: exact, then containment
    scored by similarity — generic words ('Teatro', 'Museu Nacional') that would
    match many seeds, or weak partial matches, resolve to None rather than to
    whatever happens to come first in the file."""
    key = _nt(name)
    if len(key) < 4:
        return None
    hit = idx.get(key)
    if hit is not None or len(key) < 6:
        return hit
    cands = [(difflib.SequenceMatcher(None, key, k).ratio(), k, s)
             for k, s in idx.items() if len(k) >= 6 and (key in k or k in key)]
    if len(cands) == 1:
        return cands[0][2]
    if not cands:
        return None
    cands.sort(key=lambda c: c[0], reverse=True)
    return cands[0][2] if cands[0][0] >= 0.75 else None


# ---------- geo: coordinates + neighbourhood ----------
# Greater-Lisbon bounding box — coords outside it are bad data (AgendaLX has a
# few venues geocoded to other countries) and get dropped.
_LIS_BBOX = (38.40, 39.05, -9.60, -8.85)  # lat_lo, lat_hi, lng_lo, lng_hi


def valid_lisbon_coord(lat, lng) -> bool:
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    lo_la, hi_la, lo_ln, hi_ln = _LIS_BBOX
    return lo_la <= lat <= hi_la and lo_ln <= lng <= hi_ln


# The 24 Lisbon freguesias (2013) → the site's display neighbourhood. A parish can
# span several display areas (Misericórdia = Bairro Alto/Chiado/Cais), so this is
# the COARSE fallback; an alias match on the address/venue name runs first and is
# finer. Keys are accent-folded parish names.
PARISH_TO_NEIGH = {
    "ajuda": ("Belém", "city"), "alcantara": ("Alcântara", "city"),
    "alvalade": ("Alvalade", "city"), "areeiro": ("Alvalade", "city"),
    "arroios": ("Arroios", "city"), "avenidas novas": ("Avenidas Novas", "city"),
    "beato": ("Beato", "city"), "belem": ("Belém", "city"),
    "benfica": ("Lumiar", "city"), "campo de ourique": ("Campo de Ourique", "city"),
    "campolide": ("Campolide", "city"), "carnide": ("Lumiar", "city"),
    "estrela": ("Estrela", "city"), "lumiar": ("Lumiar", "city"),
    "marvila": ("Marvila", "city"), "misericordia": ("Bairro Alto", "city"),
    "olivais": ("Parque das Nações", "city"), "parque das nacoes": ("Parque das Nações", "city"),
    "penha de franca": ("Penha de França", "city"), "santa clara": ("Lumiar", "city"),
    "santa maria maior": ("Baixa", "city"), "santo antonio": ("Avenida da Liberdade", "city"),
    "sao domingos de benfica": ("Lumiar", "city"), "sao vicente": ("Graça", "city"),
}


def _alias_index(tax: dict):
    """[(folded_alias, name, zone)] sorted longest-alias-first so a specific
    alias ('cais do sodré') wins over a short one ('cais')."""
    out = []
    for n in tax.get("neighbourhoods", []):
        for a in n.get("aliases", []):
            out.append((_fold_spaces(a), n["name"], n.get("zone")))
    out.sort(key=lambda x: len(x[0]), reverse=True)
    return out


def _fold_spaces(s: str) -> str:
    """Lowercase + accent-fold but KEEP spaces (for substring alias matching)."""
    f = unicodedata.normalize("NFKD", str(s or ""))
    f = "".join(c for c in f if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", f).strip()


def alias_neighbourhood(text: str, alias_idx) -> tuple:
    """Match a free-text address/venue name to a display neighbourhood by its
    taxonomy aliases (word-boundary, longest alias first). Returns (name, zone)
    or (None, None)."""
    blob = " " + _fold_spaces(text) + " "
    for alias, name, zone in alias_idx:
        if alias and (" " + alias + " ") in blob:
            return name, zone
    return None, None


def _pip(lng: float, lat: float, ring) -> bool:
    """Ray-casting point-in-polygon for one ring of [lng,lat] pairs."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and \
                (lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _in_feature(lng, lat, geom) -> bool:
    t = geom.get("type")
    coords = geom.get("coordinates") or []
    polys = coords if t == "MultiPolygon" else ([coords] if t == "Polygon" else [])
    for poly in polys:
        if not poly:
            continue
        if _pip(lng, lat, poly[0]) and not any(_pip(lng, lat, hole) for hole in poly[1:]):
            return True
    return False


def parish_neighbourhood(lat, lng, geojson: dict, name_prop: str) -> tuple:
    """(neighbourhood, zone) for a coordinate, via point-in-polygon against the
    freguesia GeoJSON then PARISH_TO_NEIGH. (None, None) if no parish contains it."""
    if not valid_lisbon_coord(lat, lng):
        return None, None
    latf, lngf = float(lat), float(lng)
    for feat in geojson.get("features", []):
        if _in_feature(lngf, latf, feat.get("geometry") or {}):
            parish = _fold_spaces((feat.get("properties") or {}).get(name_prop, ""))
            return PARISH_TO_NEIGH.get(parish, (None, None))
    return None, None


FREGUESIAS_PATH = ROOT / "sources" / "lisboa-freguesias.geojson"


def load_freguesias() -> tuple:
    """(geojson, name_prop) for the Lisbon parish polygons, or (None, None)."""
    if not FREGUESIAS_PATH.exists():
        return None, None
    try:
        g = json.loads(FREGUESIAS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    feats = g.get("features", [])
    prop = "NOME"
    if feats and "NOME" not in (feats[0].get("properties") or {}):
        prop = next((k for k, v in (feats[0].get("properties") or {}).items()
                     if isinstance(v, str)), "NOME")
    return g, prop


def fill_neighbourhoods(events: list[dict], geojson: dict, name_prop: str) -> int:
    """Backfill the neighbourhood from each event's own coordinates (parish
    point-in-polygon) when the venue name didn't resolve to one. Returns the
    count filled. This catches coord-bearing connector events (Fever/BOL/Xceed/
    Ticketline) whose venue isn't in the directory."""
    if not geojson:
        return 0
    filled = 0
    for e in events:
        if e.get("neighbourhood") or not e.get("lat"):
            continue
        n, z = parish_neighbourhood(e.get("lat"), e.get("lng"), geojson, name_prop)
        if n:
            e["neighbourhood"], e["zone"], filled = n, z, filled + 1
    return filled


def load_venues() -> dict:
    """Venue directory (sources/venues.json): _nt(name) -> {name,lat,lng,
    neighbourhood,zone,address}. Empty dict if not built yet."""
    p = ROOT / "sources" / "venues.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("venues", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def venue_geo(name: str, venues: dict) -> dict | None:
    """Look a venue name up in the directory: exact _nt, then unique containment."""
    if not name or not venues:
        return None
    key = _nt(name)
    if len(key) < 4:
        return None
    hit = venues.get(key)
    if hit is not None or len(key) < 6:
        return hit
    cands = [v for k, v in venues.items() if len(k) >= 6 and (key in k or k in key)]
    return cands[0] if len(cands) == 1 else None


def looks_like_date(name: str) -> bool:
    """True for 'venue' names that are really dates or scheduling notes a model
    misread from an agenda page (e.g. '19–22 Nov 2026', '31 Jul–9 Aug 2026',
    '2026 dates TBC') — these polluted the seed list before."""
    n = (name or "").strip()
    mon = r"[\wçÇ]{3,9}\.?"
    return bool(
        re.fullmatch(r"[\d\s.–—-]+", n)
        or re.search(rf"\b\d{{1,2}}\s*[–—-]\s*\d{{1,2}}\s+{mon}(\s+20\d\d)?\b", n)
        or re.search(rf"(?i)\b\d{{1,2}}\s+{mon}\s*[–—-]\s*\d{{1,2}}\s+{mon}(\s+20\d\d)?\b", n)
        or re.search(r"(?i)^\d{1,2}\s+[\wçÇ]{3,9}\.?\s+20\d\d$", n)
        or re.search(r"^20\d\d\b", n)
        or re.search(r"(?i)\bdates?\s+tbc\b|\bdatas?\s+por\s+confirmar\b", n)
    )


def resolve_url(url: str | None, base: str | None) -> str | None:
    """Validate/absolutise an event URL coming from a model or a feed. Junk
    ('ver bilhetes', bracket-wrapped copies, unparseable strings) -> None so the
    caller can fall back to the venue homepage."""
    u = str(url or "").strip().strip("[]<>")
    if not u or re.search(r"\s", u):
        return None
    try:
        if not re.match(r"^https?://", u, re.I):
            u = urljoin(base or "", u)
        if not re.match(r"^https?://", u, re.I) or "[" in u or "]" in u:
            return None
        return u if urlparse(u).netloc else None
    except ValueError:
        return None

def make_event(*, title, source, topic, mon, window_end, start_d, end_d, has_time, start_iso,
               price, url, description, language, categories,
               venue_name=None, neighbourhood=None, zone=None,
               lat=None, lng=None, lineup=None, links=None, prov=None, ongoing=None) -> dict | None:
    if not title or not start_d:
        return None
    if not overlaps_window(start_d, end_d, mon, window_end):
        return None
    days, ongoing_auto = days_in_window(start_d, end_d, mon, window_end)
    if not days:
        return None
    # connectors that clamp a run's start to the window (so the card sorts right)
    # would otherwise lose the "started earlier" signal — let them force it.
    if ongoing is None:
        ongoing = ongoing_auto
    venue = re.sub(r"\s+", " ", str(venue_name or source["name"])).strip()[:120]
    title = clean_title(title, venue)
    if not title:
        return None
    eid = hashlib.sha1(f"{source['id']}|{title}|{start_iso}".encode()).hexdigest()[:12]
    return {
        "id": eid, "title": title, "topic": topic,
        "categories": categories, "venue": venue, "source_id": source["id"],
        "neighbourhood": neighbourhood or source.get("neighbourhood"),
        "zone": zone or source.get("zone"),
        "lat": lat, "lng": lng,
        "start": start_iso, "end": (end_d.isoformat() if end_d else None),
        "all_day": not has_time, "ongoing": ongoing, "days": days,
        "price": price, "language": language or ["pt"],
        "url": resolve_url(url, source.get("website")) or source.get("website"),
        "source": source.get("provider") or "site",
        "description": clean_description(description, title, venue), "image": None,
        "lineup": lineup or None, "links": links or None,
        "prov": prov or None,   # per-field provenance (price/image/...): set by the cross-source merge
    }


def _has_path(url: str | None) -> bool:
    try:
        return bool(urlparse(url or "").path.strip("/"))
    except ValueError:
        return False


def event_coord_key(e: dict) -> str | None:
    """~110m coordinate-grid key (round to 3 decimals) for cross-source venue
    matching, or None when the event has no valid Lisbon coordinate. Same
    coordinate = same place even when two sources spell the venue differently."""
    lat, lng = e.get("lat"), e.get("lng")
    if valid_lisbon_coord(lat, lng):
        return f"{round(float(lat), 3)}|{round(float(lng), 3)}"
    return None


# sources whose price is the most authoritative (exact ticket price)
_TICKETING = {"bol", "dice", "xceed", "shotgun", "ticketline", "fever"}


def dedupe(events: list[dict], sources: list[dict] | None = None) -> list[dict]:
    """Collapse the same real-world event found more than once.

    Two copies only ever merge when they are at the same place: same extracted
    venue name, or same crawled page where at least one copy carries a generic
    venue label (the seed entry's own name, i.e. nothing was extracted — this is
    how duplicate seed entries and aggregator relists look). Same-titled events
    at genuinely different venues (two jam sessions on one night, one agenda
    page listing a concert at the Sé and another at São Roque) never merge.

    Pass 1 — exact title+date per page; pass 2 — exact title+date per venue;
    pass 3 — near-identical titles on the same date, same-place rule above.
    The most informative copy survives; its missing url/price/description/time
    and any wider date range are filled in from the duplicates it absorbs."""
    site_of, name_of, agg = {}, {}, set()
    for s in sources or []:
        site_of[s["id"]] = site_key(s.get("website") or "")
        name_of[s["id"]] = _nt(s.get("name") or "")
        if s.get("topic") == "guides":
            agg.add(s["id"])

    def date_of(e):
        return (e.get("start") or "")[:10]

    def generic(e):
        # venue label is just the seed entry's name — not extracted from the page
        src_name = name_of.get(e["source_id"])
        return src_name is None or _nt(e["venue"]) == src_name

    def score(e):
        return (2 * _has_path(e.get("url")) + 2 * (e["source_id"] not in agg)
                + (not e.get("all_day")) + bool((e.get("price") or {}).get("text"))
                + min(len(e.get("description") or ""), 200) / 200
                - 5 * looks_like_date(e.get("venue") or ""))

    def price_rank(e):
        # ticketing has the exact price; the venue's own feed beats an aggregator
        if e.get("source") in _TICKETING:
            return 3
        return 1 if e["source_id"] in agg else 2

    def note_prov(keep, field, ev):
        prov = keep.get("prov") or {}
        prov[field] = ev.get("source")
        keep["prov"] = prov

    def absorb(keep, other):
        if not _has_path(keep.get("url")) and _has_path(other.get("url")):
            keep["url"] = other["url"]
        # keep every distinct event/buy link besides the primary url (links[])
        extra = [u for u in dict.fromkeys(
                    [other.get("url"), *(keep.get("links") or []), *(other.get("links") or [])])
                 if _has_path(u) and u != keep.get("url")]
        if extra:
            keep["links"] = list(dict.fromkeys((keep.get("links") or []) + extra)) or None
        if len(other.get("description") or "") > len(keep.get("description") or ""):
            keep["description"] = other["description"]
            note_prov(keep, "description", other)
        # price: fill if missing, OR upgrade when the other source ranks higher
        op, kp = (other.get("price") or {}).get("text"), (keep.get("price") or {}).get("text")
        if op and (not kp or price_rank(other) > price_rank(keep)):
            keep["price"] = other["price"]
            note_prov(keep, "price", other)
        if not keep.get("image") and other.get("image"):
            keep["image"] = other["image"]
            note_prov(keep, "image", other)
        if not keep.get("lineup") and other.get("lineup"):
            keep["lineup"] = other["lineup"]
        if not event_coord_key(keep) and event_coord_key(other):
            keep["lat"], keep["lng"] = other.get("lat"), other.get("lng")
        if not keep.get("neighbourhood") and other.get("neighbourhood"):
            keep["neighbourhood"], keep["zone"] = other.get("neighbourhood"), other.get("zone")
        if keep.get("all_day") and not other.get("all_day") and date_of(keep) == date_of(other):
            keep["start"], keep["all_day"] = other["start"], False
        if len(other.get("days") or []) > len(keep.get("days") or []):  # keep the full run
            keep["days"] = other["days"]
        if (other.get("end") or "") > (keep.get("end") or ""):
            keep["end"] = other["end"]
        keep["ongoing"] = bool(keep.get("ongoing") or other.get("ongoing"))

    def collapse(evs, keyfn):
        groups: dict = {}
        for e in evs:
            groups.setdefault(keyfn(e), []).append(e)
        out = []
        for grp in groups.values():
            grp.sort(key=score, reverse=True)
            for loser in grp[1:]:
                absorb(grp[0], loser)
            out.append(grp[0])
        return out

    # pass 1: same page — but extracted venues must agree (one agenda page can
    # hold same-titled events at different venues)
    events = collapse(events, lambda e: (_nt(e["title"]), date_of(e),
                                         site_of.get(e["source_id"]) or e["source_id"],
                                         "" if generic(e) else _nt(e["venue"])))
    events = collapse(events, lambda e: (_nt(e["title"]), date_of(e), _nt(e["venue"])))

    # fuzzy pass, bucketed by date so the O(n²) compare stays tiny
    by_date: dict = {}
    for e in events:
        by_date.setdefault(date_of(e), []).append(e)
    out = []
    for evs in by_date.values():
        evs.sort(key=score, reverse=True)
        kept: list[dict] = []
        for e in evs:
            nt = _nt(e["title"])
            cke = event_coord_key(e)
            dup = None
            for k in kept:
                knt = _nt(k["title"])
                # same coordinate = same place; with that confidence a looser
                # title match (0.82) is enough, else require 0.9 / a long prefix
                coord_same = bool(cke) and cke == event_coord_key(k)
                ratio = difflib.SequenceMatcher(None, nt, knt).ratio()
                near = (ratio >= 0.9 or (coord_same and ratio >= 0.82)
                        or (min(len(nt), len(knt)) >= 12 and (nt.startswith(knt) or knt.startswith(nt))))
                if not near:
                    continue
                venue_same = _nt(e["venue"]) == _nt(k["venue"])
                same_site = bool(site_of.get(e["source_id"])) and \
                    site_of.get(e["source_id"]) == site_of.get(k["source_id"])
                # venue_key (coords) is the strongest same-place signal; name and
                # same-page/generic remain as fallbacks when coords are absent
                same_place = coord_same or venue_same or (same_site and (generic(e) or generic(k)))
                # an aggregator copy with no extracted venue is a relist of the near event
                agg_relist = (e["source_id"] in agg and generic(e)) or \
                             (k["source_id"] in agg and generic(k))
                if same_place or agg_relist:
                    dup = k
                    break
            if dup is None:
                kept.append(e)
            else:
                absorb(dup, e)
        out.extend(kept)
    out.sort(key=lambda x: (x["start"], x["venue"]))
    return out
