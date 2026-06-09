#!/usr/bin/env python3
"""
Apply accepted maintenance changes to the seed list.

Triggered by the admin "Apply" button → GitHub `repository_dispatch`
(event_type: apply-changes). The workflow writes the client_payload to a file
and runs:  py crawler/apply_changes.py --payload changes.json

Payload shape (produced by docs/admin/admin.js):
  { "accept_closures": [{ "id": "...", ... }],
    "accept_new":      [{ "name": "...", "url": "...", "topic": "...", "neighbourhood": "..." }] }

Closures are marked status=closed (kept for the record, crawlable disabled).
New venues are appended. Nothing is deleted.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core

SOCIAL = ("instagram.com", "facebook.com", "t.me", "tiktok.com", "wa.me", "whatsapp.com")


def slugify(s: str) -> str:
    s = s.lower()
    for a, b in {"á": "a", "à": "a", "ã": "a", "â": "a", "é": "e", "ê": "e", "í": "i",
                 "ó": "o", "ô": "o", "õ": "o", "ú": "u", "ç": "c", "&": "and"}.items():
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "venue"


def host_of(url: str) -> str:
    return re.sub(r"^https?://", "", url or "", flags=re.I).split("/")[0].lower()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=None, help="path to accepted-changes JSON")
    args = ap.parse_args()

    if args.payload:
        data = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    elif os.environ.get("CLIENT_PAYLOAD"):
        data = json.loads(os.environ["CLIENT_PAYLOAD"])
    else:
        print("no payload provided"); return
    if not isinstance(data, dict):
        print("empty/invalid payload — nothing to apply"); return

    tax = core.load_taxonomy()
    valid_topics = core.topic_ids(tax)
    cat_for_topic = {t["id"]: (t["categories"][0] if t["categories"] else 1) for t in tax["topics"]}
    payload = core.load_sources()
    sources = payload["sources"]
    by_id = {s["id"]: s for s in sources}
    used_ids = set(by_id)
    stamp = datetime.now(timezone.utc).date().isoformat()

    closed = 0
    for c in data.get("accept_closures", []):
        s = by_id.get(c.get("id"))
        if s and s.get("status") != "closed":
            s["status"] = "closed"
            s["crawlable"] = False
            s.setdefault("flags", []).append(f"closed via review {stamp}")
            closed += 1

    added = 0
    for v in data.get("accept_new", []):
        name = (v.get("name") or "").strip()
        if not name:
            continue
        sid = slugify(name)
        n = 2
        while sid in used_ids:
            sid = f"{slugify(name)}-{n}"; n += 1
        used_ids.add(sid)
        url = (v.get("url") or "").strip() or None
        host = host_of(url) if url else ""
        crawlable = bool(url) and not any(host == s or host.endswith("." + s) for s in SOCIAL)
        topic = v.get("topic") if v.get("topic") in valid_topics else "guides"
        sources.append({
            "id": sid, "name": name[:120], "area": v.get("neighbourhood") or "",
            "neighbourhood": v.get("neighbourhood"), "zone": None,
            "description": v.get("note") or "", "website": url, "other_urls": [],
            "instagram": None, "facebook": None, "handles": [],
            "provider": "generic" if crawlable else "social", "crawlable": crawlable,
            "topic": topic, "categories": [cat_for_topic.get(topic, 1)],
            "primary_category": cat_for_topic.get(topic, 1),
            "status": "active", "flags": [f"added via review {stamp}"],
            "dead_signals": 0, "added_on": stamp,
        })
        added += 1

    payload["count"] = len(sources)
    core.save_sources(payload)
    print(f"applied: {closed} closed, {added} new venues. sources now {len(sources)}.")


if __name__ == "__main__":
    main()
