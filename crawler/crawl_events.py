#!/usr/bin/env python3
"""
Sunday crawl — produces one shard's worth of next-week events.

Hybrid strategy per source: fetch the site, parse any ICS/RSS feed it advertises
(free, reliable); only if no feed events are found do we fall back to AI
extraction of the page text (Haiku-first, escalate to Sonnet, hard cost cap).

Sharded for GitHub Actions: --shard i --of n splits the source list so n jobs
run in parallel. Each writes a partial JSON; merge_week.py combines them.

  py crawler/crawl_events.py --shard 0 --of 1 --out _partials/shard-0.json
  py crawler/crawl_events.py --no-ai --limit 40 --out _partials/dry.json   # free dry-run
"""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core
import extract

def enrich_events(session, cfg, source, evs, delay, listing_html="", cap=50,
                  prov=None, client=None, tracker=None):
    """Read each event's OWN page (HTTP, no AI) and fill what the listing didn't
    have: real poster image (JSON-LD/og:image, skipping logos), ticket price
    (JSON-LD offers or a €-scan), start time and a better description.
    When structured data leaves the PRICE missing and there's AI budget left,
    one small DeepSeek read of the page fills it (the page is already fetched).
    Bounded per source; failures are silent."""
    page = core.site_key(source.get("website") or "")
    default_img = core.og_image(listing_html)  # the venue's default/logo — reject it
    use_ai = prov and client and tracker is not None and cfg["ai"].get("enrich_pages", True)
    cache, fetched = {}, 0
    for e in evs:
        u = e.get("url")
        if not u or core.site_key(u) == page:
            continue  # homepage fallback — no dedicated event page to read
        if u not in cache:
            if fetched >= cap:
                continue
            fetched += 1
            got = core.fetch(session, u, cfg)
            ok = got and got[0] < 400 and "html" in (got[1] or "")
            info = core.scrape_event_page(got[2], u, default_img) if ok else {}
            # AI reads EVERY event page (budget-gated). Structured data stays the
            # source of truth for price; the AI fills what's missing + improves
            # the description from the actual event text.
            if ok and use_ai and not tracker.exhausted():
                text = core.html_to_text(got[2], 9000)
                d = extract.extract_details(prov, client, source, text, cfg, tracker)
                if not (info.get("price") or {}).get("text"):  # JSON-LD price wins when present
                    if d.get("is_free"):
                        info["price"] = {"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
                    elif d.get("price_text"):
                        info["price"] = core.parse_price(d["price_text"])
                if not info.get("start_time") and re.match(r"^\d{1,2}:\d{2}", str(d.get("time") or "")):
                    info["start_time"] = d["time"][:5]
                if d.get("description") and len(d["description"]) > len(info.get("description") or ""):
                    info["description"] = core.clean_description(d["description"], "", "")
            cache[u] = info
            time.sleep(delay / 2)
        info = cache[u]
        if not e.get("image") and info.get("image"):
            e["image"] = info["image"]
        if not (e.get("price") or {}).get("text") and info.get("price"):
            e["price"] = info["price"]
        if e.get("all_day") and info.get("start_time"):
            e["start"] = e["start"][:10] + "T" + info["start_time"]
            e["all_day"] = False
        if len(info.get("description") or "") > len(e.get("description") or ""):
            e["description"] = info["description"]


def pick_representative(group: list[dict]) -> dict:
    """Several seed entries can point at the same web page (e.g. a venue listed
    under two names, or venues whose 'website' is an agenda page). Crawl the
    page once, attributed to the entry that best matches the domain — agenda
    sources win so events get venue names extracted from the page instead."""
    if len(group) == 1:
        return group[0]
    host = core.site_key(group[0]["website"]).split("/")[0].replace(".", "")

    def fit(s):
        filled = sum(1 for k in ("neighbourhood", "description", "instagram") if s.get(k))
        return (s.get("topic") == "guides",
                difflib.SequenceMatcher(None, core._nt(s["name"]), host).ratio(), filled)
    return max(group, key=fit)


def feed_events(session, cfg, source, html, mon, window_end):
    # ICS calendars only — they are authoritative dated events. RSS/Atom feeds are
    # usually blog articles dated "today", not events, so the AI reads those pages instead.
    out = []
    feeds = [f for f in core.discover_feeds(html, source["website"]) if f.lower().endswith(".ics")]
    for furl in feeds[:3]:
        got = core.fetch(session, furl, cfg)
        raw = core.parse_ics(got[2]) if got and got[0] == 200 else []
        for r in raw:
            parsed = core.parse_dt(r.get("start"))
            if not parsed:
                continue
            start_d, has_time, start_iso = parsed
            end_parsed = core.parse_dt(r.get("end"))
            ev = core.make_event(
                title=r.get("title"), source=source, topic=source.get("topic") or "guides",
                mon=mon, window_end=window_end, start_d=start_d,
                end_d=(end_parsed[0] if end_parsed else None), has_time=has_time, start_iso=start_iso,
                price=core.detect_price(r.get("desc") or ""), url=r.get("url"),
                description=r.get("desc"), language=["pt"], categories=source.get("categories", [])[:2],
            )
            if ev:
                out.append(ev)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="cap sources (testing)")
    ap.add_argument("--no-ai", action="store_true", help="feeds only, no API calls / no cost")
    ap.add_argument("--max-cost", type=float, default=None, help="override per-run USD cap")
    ap.add_argument("--today", default=None, help="override date YYYY-MM-DD (testing)")
    args = ap.parse_args()

    cfg = core.load_config()
    tax = core.load_taxonomy()
    sources = core.load_sources()["sources"]

    today = date.fromisoformat(args.today) if args.today else date.today()
    mon, display_sun, window_end = core.week_window(today, cfg["week"].get("lookahead_days", 7))

    crawlable = [s for s in sources if s.get("crawlable") and s.get("website")
                 and s.get("status") in ("active", "renovation")]
    # one crawl per distinct page, sharded by a stable hash of the page so
    # same-site entries can never land in different shards and duplicate work
    by_site: dict[str, list[dict]] = {}
    for s in crawlable:
        by_site.setdefault(core.site_key(s["website"]), []).append(s)
    targets = [pick_representative(g) for g in by_site.values()]
    skipped = len(crawlable) - len(targets)
    if skipped:
        print(f"[dedup] {skipped} seed entries share a page with another entry — each page is crawled once")
    shard = [s for s in targets
             if int(hashlib.sha1(core.site_key(s["website"]).encode()).hexdigest(), 16) % args.of == args.shard]
    if args.limit:
        shard = shard[:args.limit]
    venues_idx = core.venues_index(sources)

    ai_enabled = cfg["ai"].get("enabled", True) and not args.no_ai
    # run cap respects the monthly ceiling, then splits across parallel shards
    if args.max_cost is not None:
        cap = args.max_cost
    else:
        run_cap, month_spent = core.effective_run_cap(cfg, today)
        cap = run_cap / max(args.of, 1)
        if run_cap <= 0:
            print(f"[info] monthly AI budget exhausted (${month_spent:.2f} spent) — feeds-only run.")
            ai_enabled = False
    tracker = extract.CostTracker(cap)
    prov = client = None
    if ai_enabled:
        try:
            prov, client = extract.get_client(cfg)
        except Exception as e:
            print(f"[warn] AI disabled ({e}); running feeds-only.")
            ai_enabled = False

    session = core.make_session(cfg)
    events, tried, ai_calls, errors = [], 0, 0, 0
    delay = cfg["crawl"].get("polite_delay_ms", 800) / 1000.0

    for s in shard:
        tried += 1
        got = core.fetch(session, s["website"], cfg)
        if not got or got[0] >= 400:
            errors += 1
            continue
        _, ct, html = got
        if "html" not in ct and "xml" not in ct and not (html or "").strip().startswith("<"):
            continue  # skip non-HTML/non-feed bodies (PDF/JSON/image): no events, no AI spend
        evs = feed_events(session, cfg, s, html, mon, window_end)
        if not evs and ai_enabled and not tracker.exhausted():
            page_links: set = set()
            text = core.html_to_text(html, cfg["ai"].get("max_chars_per_page", 18000),
                                     base_url=s["website"], keep_links=True, link_sink=page_links)
            evs = extract.extract(prov, client, s, text, mon, window_end, cfg, tax, tracker,
                                  venues_idx, page_links)
            ai_calls += 1
        if evs:
            enrich_events(session, cfg, s, evs, delay, listing_html=html,
                          prov=prov, client=client, tracker=tracker)
        events.extend(evs)
        time.sleep(delay)

    events = core.dedupe(events, sources)
    core.drop_shared_images(events)
    payload = {
        "week_start": mon.isoformat(), "week_end": display_sun.isoformat(),
        "shard": args.shard, "of": args.of, "event_count": len(events),
        "meta": {"sources_tried": tried, "errors": errors, "ai_calls": ai_calls,
                 "ai_cost_usd": round(tracker.spent, 4), "ai_enabled": ai_enabled},
        "events": events,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"shard {args.shard}/{args.of}: {len(events)} events from {tried} sources "
          f"({errors} unreachable, {ai_calls} AI calls, ${tracker.spent:.3f}) -> {out}")


if __name__ == "__main__":
    main()
