#!/usr/bin/env python3
"""
Fetch the structured connectors over a WIDE horizon and write one partial.

Runs once per crawl (not sharded — the connectors are a handful of fast API
calls). The partial it writes is picked up by merge_week.py, which upserts the
events into the persistent pool (docs/data/pool.json) and then filters the pool
to the displayed week. No AI, no cost.

  py crawler/fetch_connectors.py --out _partials/connectors.json
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core
import connectors


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--today", default=None, help="override date YYYY-MM-DD (testing)")
    ap.add_argument("--horizon", type=int, default=None, help="override horizon days")
    args = ap.parse_args()

    cfg = core.load_config()
    tax = core.load_taxonomy()
    sources = core.load_sources()["sources"]

    today = date.fromisoformat(args.today) if args.today else date.today()
    mon = core.target_monday(today)
    horizon = args.horizon or cfg.get("connectors", {}).get("horizon_days", 75)
    # the pool window starts at "today" (so ongoing exhibitions are kept) and runs
    # out to mon + horizon; merge_week reframes each event to the displayed week.
    pool_start = min(mon, today)
    window_end = mon + timedelta(days=horizon)

    session = core.make_session(cfg)
    events, statuses = connectors.run_all(session, cfg, tax, sources, pool_start, window_end)
    # raw per-connector counts (before cross-source dedupe) drive silent-shrink
    # detection — a connector returning far fewer than its rolling median is flagged
    from collections import Counter
    raw_counts = Counter(e.get("source") for e in events)
    stamp = today.isoformat()
    statuses = core.update_connector_health(raw_counts, statuses, stamp)

    events = core.dedupe(events, sources + connectors.connector_sources())

    payload = {
        "kind": "connectors",
        "week_start": mon.isoformat(), "week_end": (mon + timedelta(days=6)).isoformat(),
        "horizon_end": window_end.isoformat(),
        "event_count": len(events),
        "meta": {"connector_status": statuses,
                 "raw_counts": dict(raw_counts),
                 "events_by_connector": {c: sum(1 for e in events if e.get("source") == c) for c in statuses}},
        "events": events,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    bits = ", ".join(f"{k}:{v}" for k, v in statuses.items())
    print(f"connectors -> {len(events)} events over {pool_start}..{window_end} ({bits}) -> {out}")


if __name__ == "__main__":
    main()
