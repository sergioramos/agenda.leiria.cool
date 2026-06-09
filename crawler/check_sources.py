#!/usr/bin/env python3
"""
Monday maintenance — keeps the crawl list healthy.

 1. Probes every crawlable source. 404/410/unreachable increments a per-source
    "dead_signals" counter (reset to 0 when reachable, with last_seen stamped).
    After N consecutive signals a closure is *proposed* — never auto-applied.
 2. Optionally mines a few aggregators for venues not yet in the seed list and
    proposes them as new venues.

Writes the proposals to docs/data/proposed-changes/latest.json (reviewed in
/admin) and commits the updated bookkeeping in sources/sources.json. Approved
changes are applied later by apply_changes.py.

  py crawler/check_sources.py --no-ai            # probe only, no discovery
  py crawler/check_sources.py --limit 50
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core
import extract

DISCOVER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"venues": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {"name": {"type": "string"}, "url": {"type": "string"},
                       "topic": {"type": "string"}, "note": {"type": "string"}},
        "required": ["name"]}}},
    "required": ["venues"],
}


def host_of(url: str) -> str:
    h = re.sub(r"^https?://", "", url or "", flags=re.I).split("/")[0].lower()
    return h[4:] if h.startswith("www.") else h


def discover(prov, client, cfg, tax, sources, session, tracker, cap_new):
    known_names = {re.sub(r"[^a-z0-9]+", "", s["name"].lower()) for s in sources}
    known_hosts = {host_of(s.get("website") or "") for s in sources if s.get("website")}
    aggregators = [s for s in sources if s.get("topic") == "guides" and s.get("crawlable")][:6]
    found = []
    tids = [t["id"] for t in tax["topics"]]
    sys_text = ("Extrai nomes de locais/organizadores de eventos em Lisboa mencionados nesta "
                "página de agenda, com o respetivo URL próprio (não o da agenda) quando existir. "
                f"topic deve ser um de: {', '.join(tids)}.")
    hint = '{"venues":[{"name":"...","url":"","topic":"music","note":""}]}'
    for agg in aggregators:
        if tracker.exhausted() or len(found) >= cap_new:
            break
        got = core.fetch(session, agg["website"], cfg)
        if not got or got[0] >= 400:
            continue
        text = core.html_to_text(got[2], cfg["ai"].get("max_chars_per_page", 18000))
        data = extract.json_call(prov, client, cfg["ai"]["model_cheap"], sys_text, text,
                                 DISCOVER_SCHEMA, hint, 2000, tracker)
        venues = (data or {}).get("venues", [])
        for v in venues:
            nk = re.sub(r"[^a-z0-9]+", "", (v.get("name") or "").lower())
            hk = host_of(v.get("url") or "")
            if not nk or nk in known_names or (hk and hk in known_hosts):
                continue
            known_names.add(nk)
            if hk:
                known_hosts.add(hk)
            found.append({"name": v["name"][:120], "neighbourhood": None,
                          "topic": v.get("topic") if v.get("topic") in tids else None,
                          "url": v.get("url") or None, "found_via": agg["name"],
                          "note": (v.get("note") or "")[:120]})
            if len(found) >= cap_new:
                break
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--out-dir", default="docs/data")
    ap.add_argument("--generated-at", default=None)
    args = ap.parse_args()

    cfg = core.load_config()
    tax = core.load_taxonomy()
    payload = core.load_sources()
    sources = payload["sources"]
    threshold = cfg["maintenance"].get("closure_signals_to_flag", 2)
    session = core.make_session(cfg)
    today = datetime.now(timezone.utc).date().isoformat()

    crawlable = [s for s in sources if s.get("crawlable") and s.get("website")]
    if args.limit:
        crawlable = crawlable[:args.limit]

    for s in crawlable:
        got = core.fetch(session, s["website"], cfg)
        status = got[0] if got else None
        if status is None or status in (404, 410):
            s["dead_signals"] = s.get("dead_signals", 0) + 1
        elif status < 400:
            s["dead_signals"] = 0
            s["last_seen"] = today
        time.sleep(cfg["crawl"].get("polite_delay_ms", 800) / 1000.0)

    closures = []
    for s in sources:
        suspect = s.get("dead_signals", 0) >= threshold and s.get("status") == "active"
        prflagged = s.get("status") in ("possibly_closed", "at_risk", "closing")
        if suspect or prflagged:
            reason = (f"{s['dead_signals']} sinais de inacessibilidade" if suspect
                      else (s["flags"][0] if s.get("flags") else "assinalado para revisão"))
            closures.append({"id": s["id"], "name": s["name"], "neighbourhood": s.get("neighbourhood"),
                             "reason": reason, "current_status": s.get("status")})

    new_venues = []
    tracker = extract.CostTracker(cfg["ai"].get("max_run_cost_usd", 2.0))
    if not args.no_ai and cfg["maintenance"].get("discover_new", True) and cfg["ai"].get("enabled", True):
        try:
            prov, client = extract.get_client(cfg)
            new_venues = discover(prov, client, cfg, tax, sources, session, tracker, cap_new=20)
        except Exception as e:
            print(f"[warn] discovery skipped ({e})")

    # commit bookkeeping (dead_signals/last_seen) but DO NOT change status here
    core.save_sources(payload)

    gen = args.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    proposed = {"generated_at": gen, "is_sample": False,
                "closures": closures, "new_venues": new_venues}
    out = Path(args.out_dir) / "proposed-changes"
    out.mkdir(parents=True, exist_ok=True)
    (out / "latest.json").write_text(json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"checked {len(crawlable)} sources -> {len(closures)} proposed closures, "
          f"{len(new_venues)} new venues (${tracker.spent:.3f}).")


if __name__ == "__main__":
    main()
