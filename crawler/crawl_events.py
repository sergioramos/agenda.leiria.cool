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
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core
import extract


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
    shard = [s for i, s in enumerate(crawlable) if i % args.of == args.shard]
    if args.limit:
        shard = shard[:args.limit]

    ai_enabled = cfg["ai"].get("enabled", True) and not args.no_ai
    # split the per-run cost ceiling across parallel shards so the aggregate stays within the cap
    cap = (args.max_cost if args.max_cost is not None
           else cfg["ai"].get("max_run_cost_usd", 2.0) / max(args.of, 1))
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
            text = core.html_to_text(html, cfg["ai"].get("max_chars_per_page", 18000))
            evs = extract.extract(prov, client, s, text, mon, window_end, cfg, tax, tracker)
            ai_calls += 1
        events.extend(evs)
        time.sleep(delay)

    events = core.dedupe(events)
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
