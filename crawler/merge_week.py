#!/usr/bin/env python3
"""
Merge shard partials into one published week file and update the archive index.

  py crawler/merge_week.py --partials-dir _partials --out-dir docs/data
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import core


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--partials-dir", required=True)
    ap.add_argument("--out-dir", default="docs/data")
    ap.add_argument("--generated-at", default=None, help="ISO timestamp (CI passes one)")
    args = ap.parse_args()

    pdir = Path(args.partials_dir)
    partials = [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(pdir.rglob("*.json"))]
    if not partials:
        print("no partials found — nothing to merge")
        return

    week_start = partials[0]["week_start"]
    week_end = partials[0]["week_end"]
    events, cost, tried, ai_calls = [], 0.0, 0, 0
    for p in partials:
        events.extend(p.get("events", []))
        m = p.get("meta", {})
        cost += m.get("ai_cost_usd", 0)
        tried += m.get("sources_tried", 0)
        ai_calls += m.get("ai_calls", 0)

    cfg = core.load_config()
    src = core.load_sources()["sources"]
    events = core.dedupe(events, src)
    core.drop_shared_images(events)  # logos/defaults shared across shards

    # NEVER publish an empty week — a failed crawl (e.g. the AI API down) must not
    # wipe the live site. Keep whatever is already published.
    if not events:
        print(f"[abort] 0 events for {week_start} (AI calls {ai_calls}, ${cost:.2f}) — "
              "likely an API failure; keeping the previously published week.")
        return
    crawlable = sum(1 for s in src if s.get("crawlable") and s.get("status") in ("active", "renovation"))
    gen = args.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")

    week = {
        "week_start": week_start, "week_end": week_end, "generated_at": gen,
        "is_sample": False, "source_count": crawlable, "event_count": len(events),
        "meta": {"sources_tried": tried, "ai_calls": ai_calls, "ai_cost_usd": round(cost, 4)},
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
    weeks.append({"start": week_start, "end": week_end, "file": f"{week_start}.json",
                  "event_count": len(events), "is_sample": False, "generated_at": gen})
    weeks.sort(key=lambda w: w["start"], reverse=True)
    keep = cfg["output"].get("keep_weeks", 104)
    for stale in weeks[keep:]:
        f = weeks_dir / stale["file"]
        if f.exists():
            f.unlink()
    weeks = weeks[:keep]
    idx_path.write_text(json.dumps({"weeks": weeks}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"merged {len(partials)} shards -> {len(events)} events for {week_start}..{week_end} "
          f"(${cost:.3f}, {ai_calls} AI calls). index now lists {len(weeks)} week(s).")


if __name__ == "__main__":
    main()
