#!/usr/bin/env python3
"""
Merge shard + connector partials into one published week and update the archive.

Flow:
  1. read all partials (HTML/AI shards + the structured-connector partial);
  2. upsert the connector events into the persistent pool (docs/data/pool.json),
     expire past entries, save the pool — this is the wide-horizon backbone that
     survives a failed run and accumulates multi-day/advance events;
  3. publish = (pool events overlapping the displayed week) + (this run's shard
     events), each reframed to the week, then cross-source deduped;
  4. never publish an empty week (a failed crawl must not wipe the live site).

  py crawler/merge_week.py --partials-dir _partials --out-dir docs/data
"""
from __future__ import annotations
import argparse
import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core
import connectors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--partials-dir", required=True)
    ap.add_argument("--out-dir", default="docs/data")
    ap.add_argument("--generated-at", default=None, help="ISO timestamp (CI passes one)")
    ap.add_argument("--today", default=None, help="override date YYYY-MM-DD (testing)")
    args = ap.parse_args()

    pdir = Path(args.partials_dir)
    partials = []
    for p in sorted(pdir.rglob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[skip] unreadable partial {p.name}: {e}")
            continue
        # only real partials (a stray dump in _partials must not crash the merge)
        if isinstance(d, dict) and (d.get("events") is not None or d.get("kind")):
            partials.append(d)
        else:
            print(f"[skip] {p.name} is not a partial")
    if not partials:
        print("no partials found — nothing to merge")
        return

    cfg = core.load_config()
    src = core.load_sources()["sources"]
    dedupe_sources = src + connectors.connector_sources()

    week_start = next((p["week_start"] for p in partials if p.get("week_start")), None)
    if not week_start:
        print("no week_start in partials — nothing to merge")
        return
    mon = date.fromisoformat(week_start)
    lookahead = cfg["week"].get("lookahead_days", 7)
    display_sun = mon + timedelta(days=6)
    window_end = mon + timedelta(days=max(lookahead, 7) - 1)
    today = date.fromisoformat(args.today) if args.today else date.today()

    # split connector partial(s) from HTML/AI shard partials
    shard_events, connector_events, statuses = [], [], {}
    cost, tried, ai_calls = 0.0, 0, 0
    for p in partials:
        m = p.get("meta", {})
        if p.get("kind") == "connectors":
            connector_events.extend(p.get("events", []))
            statuses.update(m.get("connector_status", {}))
        else:
            shard_events.extend(p.get("events", []))
            cost += m.get("ai_cost_usd", 0)
            tried += m.get("sources_tried", 0)
            ai_calls += m.get("ai_calls", 0)

    # ---- pool: upsert this run's connector events, expire the past, persist ----
    pool = core.load_pool()
    stamp = (args.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"))
    by_conn: dict = {}
    for e in connector_events:
        by_conn.setdefault(e.get("source") or "connectors", []).append(e)
    for conn, evs in by_conn.items():
        core.pool_upsert(pool, evs, conn, stamp)
    removed = core.pool_expire(pool, today)
    pool["updated_at"] = stamp
    core.save_pool(pool)
    print(f"[pool] +{len(connector_events)} from {len(by_conn)} connector(s), "
          f"-{removed} expired, {len(pool['events'])} total")

    # ---- build the publish set for the displayed week ----
    # reframe to the DISPLAYED Mon-Sun (display_sun), not the wide pool horizon —
    # otherwise an event past Sunday (when lookahead_days > 7) gets a bogus day chip.
    pooled = core.reframe_window(core.pool_events(pool), mon, display_sun)
    shards = core.reframe_window(shard_events, mon, display_sun)
    events = core.dedupe(pooled + shards, dedupe_sources)
    core.drop_shared_images(events)
    # backfill the neighbourhood from coordinates for events whose venue name
    # didn't resolve (Fever/BOL/Xceed/Ticketline carry coords but odd venue names)
    geojson, name_prop = core.load_freguesias()
    nb = core.fill_neighbourhoods(events, geojson, name_prop)
    if nb:
        print(f"[neigh] backfilled {nb} neighbourhoods from coordinates")

    # NEVER publish an empty week — a failed crawl (e.g. the AI API down) must not
    # wipe the live site. Keep whatever is already published.
    if not events:
        print(f"[abort] 0 events for {week_start} — keeping the previously published week.")
        return

    crawlable = sum(1 for s in src if s.get("crawlable") and s.get("status") in ("active", "renovation"))
    gen = args.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    events_by_connector = {c: sum(1 for e in events if e.get("source") == c) for c in statuses}

    week = {
        "week_start": week_start, "week_end": display_sun.isoformat(), "generated_at": gen,
        "is_sample": False, "source_count": crawlable, "event_count": len(events),
        "meta": {"sources_tried": tried, "ai_calls": ai_calls, "ai_cost_usd": round(cost, 4),
                 "connector_status": statuses, "events_by_connector": events_by_connector,
                 "pool_size": len(pool["events"])},
        "events": events,
    }
    out_dir = Path(args.out_dir)
    weeks_dir = out_dir / "weeks"
    weeks_dir.mkdir(parents=True, exist_ok=True)
    (weeks_dir / f"{week_start}.json").write_text(json.dumps(week, ensure_ascii=False, indent=2), encoding="utf-8")

    # update index (replace same-week entry, drop the bundled sample, prune history)
    idx_path = weeks_dir / "index.json"
    weeks = []
    if idx_path.exists():
        weeks = json.loads(idx_path.read_text(encoding="utf-8")).get("weeks", [])
    weeks = [w for w in weeks if w["start"] != week_start and not w.get("is_sample")]
    weeks.append({"start": week_start, "end": display_sun.isoformat(), "file": f"{week_start}.json",
                  "event_count": len(events), "is_sample": False, "generated_at": gen})
    weeks.sort(key=lambda w: w["start"], reverse=True)
    keep = cfg["output"].get("keep_weeks", 104)
    for stale in weeks[keep:]:
        f = weeks_dir / stale["file"]
        if f.exists():
            f.unlink()
    weeks = weeks[:keep]
    idx_path.write_text(json.dumps({"weeks": weeks}, ensure_ascii=False, indent=2), encoding="utf-8")

    bits = ", ".join(f"{k}:{v}" for k, v in events_by_connector.items())
    print(f"merged {len(partials)} partials -> {len(events)} events for {week_start}..{display_sun} "
          f"(connectors {bits}; ${cost:.3f}, {ai_calls} AI calls). index now lists {len(weeks)} week(s).")


if __name__ == "__main__":
    main()
