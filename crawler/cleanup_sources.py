#!/usr/bin/env python3
"""
One-shot hygiene pass over sources/sources.json:

 1. Merges duplicate entries — same website page AND clearly the same name
    (one contains the other, or high similarity). The most complete entry
    survives and absorbs the other's categories/handles/urls.
 2. Deletes junk entries whose "name" is really a date misread from an agenda
    page (e.g. '19–22 Nov 2026').

Dry-run by default; pass --apply to write. Entries that share a page but are
genuinely different things (e.g. venues whose website is an agenda page) are
left alone — the crawler now visits each page only once anyway.

  py crawler/cleanup_sources.py            # report only
  py crawler/cleanup_sources.py --apply
"""
from __future__ import annotations
import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core


_STOPT = {"de", "da", "do", "das", "dos", "the", "and", "para", "com", "lisboa", "lisbon"}


def _tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[\wçãõáéíóúâê]+", name.lower())
            if len(w) >= 4 and w not in _STOPT}


def same_thing(a: dict, b: dict) -> bool:
    """Same name modulo extra words: every significant word of the shorter name
    must appear in the longer one. Distinguishing words ('Monumental' vs
    'Nimas', 'Príncipe Real' vs 'Campo Pequeno') block the merge."""
    ta, tb = _tokens(a["name"]), _tokens(b["name"])
    if not ta or not tb:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small <= big


def completeness(s: dict, host: str) -> tuple:
    filled = sum(1 for k in ("neighbourhood", "description", "instagram", "facebook",
                             "zone", "area") if s.get(k))
    host_fit = difflib.SequenceMatcher(None, core._nt(s["name"]), host).ratio()
    return (s.get("status") == "active", round(host_fit, 2), filled,
            len(s.get("categories") or []))


def merge_into(keep: dict, other: dict) -> None:
    for k in ("categories", "handles", "other_urls", "flags"):
        merged = list(dict.fromkeys((keep.get(k) or []) + (other.get(k) or [])))
        if merged:
            keep[k] = merged
    for k in ("neighbourhood", "description", "instagram", "facebook", "zone", "area"):
        if not keep.get(k) and other.get(k):
            keep[k] = other[k]
    if (other.get("last_seen") or "") > (keep.get("last_seen") or ""):
        keep["last_seen"] = other["last_seen"]
    keep["dead_signals"] = min(keep.get("dead_signals", 0), other.get("dead_signals", 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    payload = core.load_sources()
    sources = payload["sources"]
    drop_ids: set[str] = set()

    junk = [s for s in sources if core.looks_like_date(s.get("name") or "")]
    for s in junk:
        drop_ids.add(s["id"])
        print(f"[junk]  {s['id']}  (nome é uma data: {s['name']!r})")

    by_site: dict[str, list[dict]] = {}
    for s in sources:
        if s["id"] in drop_ids or not s.get("website"):
            continue
        by_site.setdefault(core.site_key(s["website"]), []).append(s)

    for site, group in by_site.items():
        if len(group) < 2:
            continue
        host = site.split("/")[0].replace(".", "")
        group.sort(key=lambda s: completeness(s, host), reverse=True)
        keep = group[0]
        for other in group[1:]:
            if same_thing(keep, other):
                merge_into(keep, other)
                drop_ids.add(other["id"])
                print(f"[merge] {other['name']!r} -> {keep['name']!r}  ({site})")
            else:
                print(f"[keep]  {other['name']!r} e {keep['name']!r} partilham {site} mas parecem coisas distintas")

    if not drop_ids:
        print("nada a limpar.")
        return
    print(f"\n{len(drop_ids)} entradas a remover, {len(sources) - len(drop_ids)} ficam.")
    if args.apply:
        payload["sources"] = [s for s in sources if s["id"] not in drop_ids]
        payload["count"] = len(payload["sources"])
        core.save_sources(payload)
        print("aplicado em sources/sources.json")
    else:
        print("(dry-run — usa --apply para gravar)")


if __name__ == "__main__":
    main()
