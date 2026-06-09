"""
Shared helpers for the Pregoeiro crawler: config + data IO, the week window,
HTTP fetching, feed discovery/parsing (ICS + RSS/Atom), price detection,
date→day mapping, and event normalisation/dedup.

Pure-stdlib where possible; third-party deps are imported lazily so the
no-AI / feeds dry-run still works if some optional libs are missing.
"""
from __future__ import annotations
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

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

def html_to_text(html: str, max_chars: int) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript", "svg", "header", "footer", "nav", "form"]):
            tag.decompose()
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
def norm_key(title: str, day: str, venue: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:40] + "|" + (day or "") + "|" + re.sub(r"[^a-z0-9]+", "", (venue or "").lower())[:20]
    return base

def make_event(*, title, source, topic, mon, window_end, start_d, end_d, has_time, start_iso,
               price, url, description, language, categories) -> dict | None:
    if not title or not start_d:
        return None
    if not overlaps_window(start_d, end_d, mon, window_end):
        return None
    days, ongoing = days_in_window(start_d, end_d, mon, window_end)
    if not days:
        return None
    eid = hashlib.sha1(f"{source['id']}|{title}|{start_iso}".encode()).hexdigest()[:12]
    return {
        "id": eid, "title": title.strip()[:160], "topic": topic,
        "categories": categories, "venue": source["name"], "source_id": source["id"],
        "neighbourhood": source.get("neighbourhood"), "zone": source.get("zone"),
        "start": start_iso, "end": (end_d.isoformat() if end_d else None),
        "all_day": not has_time, "ongoing": ongoing, "days": days,
        "price": price, "language": language or ["pt"],
        "url": url or source.get("website"), "source": source.get("provider") or "site",
        "description": (description or "").strip()[:280], "image": None,
    }

def dedupe(events: list[dict]) -> list[dict]:
    seen, out = set(), []
    for e in sorted(events, key=lambda x: (x["start"], x["venue"])):
        k = norm_key(e["title"], e["days"][0] if e["days"] else "", e["venue"])
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out
