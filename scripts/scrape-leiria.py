#!/usr/bin/env python3
"""Scrape all past events from leiriagenda.cm-leiria.pt (server-rendered).

Everything the mock needs is on the LISTING cards (category, date+time, venue,
locality, image, title, url) — so we parse the ~168 listing pages only, no
per-event detail fetches. Output: data/leiria-events.json (raw records +
distinct category / locality / venue sets for the taxonomy step).

Deterministic, no LLM. Run: python3 scripts/scrape-leiria.py
"""
import concurrent.futures as cf
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# argv: [listing-slug] [output-file] — defaults to the past-events archive.
SLUG = sys.argv[1] if len(sys.argv) > 1 else "eventos-passados"
OUT = ROOT / "data" / (sys.argv[2] if len(sys.argv) > 2 else "leiria-events.json")
BASE = f"https://leiriagenda.cm-leiria.pt/pt/agenda/{SLUG}?page="
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 pregoeiro-opendata"

MONTHS = {  # pt abbreviations as rendered in .mes
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                print(f"  ! failed {url}: {e}", file=sys.stderr)
                return ""
    return ""


def txt(s):
    """Strip tags + collapse whitespace from an HTML fragment."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip()


def parse_cards(html):
    """Yield one raw record per event card on a listing page."""
    # each card is an <a href=".../pt/agenda/{slug}"> ... </a> wrapping .component
    for m in re.finditer(
        r'<a\s+href="https://leiriagenda\.cm-leiria\.pt/pt/agenda/([a-z0-9\-]+)"\s*>(.*?)</a>',
        html, re.S,
    ):
        slug, card = m.group(1), m.group(2)
        if slug in ("eventos-passados", "proximos-eventos") or "component-inner" not in card:
            continue
        title_m = re.search(r'class="proximo_title"[^>]*>(.*?)</span>', card, re.S)
        if not title_m:
            continue
        dia = re.search(r'class="dia"[^>]*>(.*?)</span>', card, re.S)
        mes = re.search(r'class="mes"[^>]*>(.*?)</span>', card, re.S)
        anos = [txt(a) for a in re.findall(r'class="ano"[^>]*>(.*?)</span>', card, re.S)]
        local = re.search(r'class="local"[^>]*>(.*?)</span>', card, re.S)
        localidade = re.search(r'class="localidade"[^>]*>(.*?)</span>', card, re.S)
        cats = [txt(c) for c in re.findall(r'<small[^>]*>(.*?)</small>', card, re.S)]
        # past cards lazy-load via data-src; upcoming cards inline a CSS background-image
        img = re.search(r'data-src="([^"]+)"', card) or re.search(r"background-image:\s*url\(([^)'\"]+)", card)
        alt = re.search(r'alt="([^"]*)"', card)

        year = next((a for a in anos if re.fullmatch(r"\d{4}", a)), None)
        time = next((a for a in anos if re.search(r"\d{1,2}h", a)), None)

        yield {
            "slug": slug,
            "url": f"https://leiriagenda.cm-leiria.pt/pt/agenda/{slug}",
            "title": txt(title_m.group(1)),
            "categories_raw": [c for c in cats if c],
            "venue": txt(local.group(1)) if local else None,
            "localidade": txt(localidade.group(1)) if localidade else None,
            "day": int(txt(dia.group(1))) if dia and txt(dia.group(1)).isdigit() else None,
            "month": MONTHS.get(txt(mes.group(1)).strip(".").lower()[:3]) if mes else None,
            "year": int(year) if year else None,
            "time": time,
            "image": img.group(1).strip("'\" ") if img else None,
            "alt": alt.group(1) if alt else None,
        }


def main():
    first = fetch(BASE + "1")
    last = max((int(n) for n in re.findall(r"eventos-passados\?page=(\d+)", first)), default=1)
    print(f"pages: 1..{last}")

    pages = {}
    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(fetch, BASE + str(p)): p for p in range(1, last + 1)}
        for done in cf.as_completed(futs):
            pages[futs[done]] = done.result()
            if len(pages) % 25 == 0:
                print(f"  fetched {len(pages)}/{last}")

    seen, records = set(), []
    for p in sorted(pages):
        for rec in parse_cards(pages[p]):
            if rec["slug"] in seen:
                continue
            seen.add(rec["slug"])
            rec["id"] = hashlib.sha1(rec["slug"].encode()).hexdigest()[:12]
            if rec["year"] and rec["month"] and rec["day"]:
                import datetime
                rec["weekday"] = WEEKDAYS[datetime.date(rec["year"], rec["month"], rec["day"]).weekday()]
            else:
                rec["weekday"] = None
            records.append(rec)

    # distinct sets for the taxonomy/judgment step
    cat_counts, loc_counts, venue_counts = {}, {}, {}
    for r in records:
        for c in r["categories_raw"]:
            cat_counts[c] = cat_counts.get(c, 0) + 1
        if r["localidade"]:
            loc_counts[r["localidade"]] = loc_counts.get(r["localidade"], 0) + 1
        if r["venue"]:
            key = (r["venue"], r["localidade"] or "")
            venue_counts[key] = venue_counts.get(key, 0) + 1

    out = {
        "count": len(records),
        "distinct_categories": dict(sorted(cat_counts.items(), key=lambda kv: -kv[1])),
        "distinct_localities": dict(sorted(loc_counts.items(), key=lambda kv: -kv[1])),
        "distinct_venues": [
            {"venue": v, "localidade": loc, "count": n}
            for (v, loc), n in sorted(venue_counts.items(), key=lambda kv: -kv[1])
        ],
        "events": records,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"ok · {len(records)} events · {len(cat_counts)} categories · "
        f"{len(loc_counts)} localities · {len(venue_counts)} venues → {OUT.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
