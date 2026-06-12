"""
Shared helpers for the Pregoeiro crawler: config + data IO, the week window,
HTTP fetching, feed discovery/parsing (ICS + RSS/Atom), price detection,
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

def parse_rss(url: str):
    """Parse an RSS/Atom feed → list of raw events (best-effort; many feeds are
    blog posts, not dated events — filtered later by the week window)."""
    out = []
    try:
        import feedparser
        d = feedparser.parse(url)
        for e in d.entries[:60]:
            when = e.get("published_parsed") or e.get("updated_parsed") or e.get("start_parsed")
            start = datetime(*when[:6]) if when else None
            out.append({
                "title": (e.get("title") or "").strip(),
                "start": start,
                "end": None,
                "url": e.get("link"),
                "desc": re.sub(r"<[^>]+>", " ", e.get("summary", ""))[:300].strip(),
            })
    except Exception:
        pass
    return out

# ---------- price + tagging ----------
FREE_RE = re.compile(r"\b(gr[áa]tis|gratuito|entrada\s+livre|free\s+entry|free\b|sem\s+custo|entrada\s+gratuita)\b", re.I)
PRICE_RE = re.compile(r"(?:€|eur\s?)\s?(\d{1,3}(?:[.,]\d{2})?)", re.I)

def detect_price(text: str) -> dict:
    t = text or ""
    if FREE_RE.search(t):
        return {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
    m = PRICE_RE.search(t)
    if m:
        amt = float(m.group(1).replace(",", "."))
        return {"is_free": False, "min": amt, "currency": "EUR", "text": f"€{m.group(1)}"}
    return {"is_free": False, "min": None, "currency": "EUR", "text": ""}

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
               venue_name=None, neighbourhood=None, zone=None) -> dict | None:
    if not title or not start_d:
        return None
    if not overlaps_window(start_d, end_d, mon, window_end):
        return None
    days, ongoing = days_in_window(start_d, end_d, mon, window_end)
    if not days:
        return None
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
        "start": start_iso, "end": (end_d.isoformat() if end_d else None),
        "all_day": not has_time, "ongoing": ongoing, "days": days,
        "price": price, "language": language or ["pt"],
        "url": resolve_url(url, source.get("website")) or source.get("website"),
        "source": source.get("provider") or "site",
        "description": (description or "").strip()[:280], "image": None,
    }


def _has_path(url: str | None) -> bool:
    try:
        return bool(urlparse(url or "").path.strip("/"))
    except ValueError:
        return False


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

    def absorb(keep, other):
        if not _has_path(keep.get("url")) and _has_path(other.get("url")):
            keep["url"] = other["url"]
        if len(other.get("description") or "") > len(keep.get("description") or ""):
            keep["description"] = other["description"]
        if not (keep.get("price") or {}).get("text") and (other.get("price") or {}).get("text"):
            keep["price"] = other["price"]
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
            dup = None
            for k in kept:
                knt = _nt(k["title"])
                near = (difflib.SequenceMatcher(None, nt, knt).ratio() >= 0.9
                        or (min(len(nt), len(knt)) >= 12 and (nt.startswith(knt) or knt.startswith(nt))))
                if not near:
                    continue
                venue_same = _nt(e["venue"]) == _nt(k["venue"])
                same_site = bool(site_of.get(e["source_id"])) and \
                    site_of.get(e["source_id"]) == site_of.get(k["source_id"])
                same_place = venue_same or (same_site and (generic(e) or generic(k)))
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
