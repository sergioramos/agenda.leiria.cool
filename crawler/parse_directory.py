#!/usr/bin/env python3
"""
Turn the human-written LISBON-EVENTS.md directory into a machine-readable seed
list (sources/sources.json) that the weekly crawler consumes.

It extracts every bolded venue/source, its area, description, website and
Instagram handle; maps it to a friendly topic + neighbourhood; detects the
fetch provider; and records any closure/at-risk flags. Cross-references
(*see Sn*) are merged into the primary entry rather than duplicated.

Run:  py crawler/parse_directory.py
Output: sources/sources.json   (+ a summary printed to the console)
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "LISBON-EVENTS.md"
TAXONOMY = ROOT / "sources" / "taxonomy.json"
OUT = ROOT / "sources" / "sources.json"

SOCIAL_HOSTS = {
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "t.me": "telegram",
    "telegram.me": "telegram",
    "tiktok.com": "tiktok",
    "wa.me": "whatsapp",
    "whatsapp.com": "whatsapp",
    "x.com": "x",
    "twitter.com": "x",
}
FEED_PROVIDERS = {
    "eventbrite.com": "eventbrite", "eventbrite.co.uk": "eventbrite", "eventbrite.pt": "eventbrite",
    "ra.co": "resident_advisor", "pt.ra.co": "resident_advisor",
    "dice.fm": "dice",
    "songkick.com": "songkick",
    "bandsintown.com": "bandsintown",
    "feverup.com": "fever",
    "meetup.com": "meetup",
    "lu.ma": "luma", "luma.com": "luma",
    "shotgun.live": "shotgun",
    "xceed.me": "xceed",
    "bol.pt": "bol", "ticketline.pt": "ticketline", "blueticket.meo.pt": "blueticket",
}

# A token is a website if it looks like host.tld(/path) with a real letter TLD.
DOMAIN_RE = re.compile(r"^(https?://)?([a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+)(/[^\s]*)?$", re.I)
TLD_OK = re.compile(r"\.[a-z]{2,}$", re.I)
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
HANDLE_RE = re.compile(r"(?:(?<=\s)|^)@([A-Za-z0-9_][A-Za-z0-9_.]+)")
CAT_HEAD_RE = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$")
FLAG_RE = re.compile(r"\[([^\]]*?(?:CLOSED|CLOSING|AT RISK|RELOCATED|SECURED|MAY HAVE|NOT IN|REFORMATTED|verify|Re-verify|opening)[^\]]*?)\]", re.I)


def load_taxonomy():
    tax = json.loads(TAXONOMY.read_text(encoding="utf-8"))
    cat_to_topic = {}
    topic_meta = {}
    for t in tax["topics"]:
        topic_meta[t["id"]] = t
        for c in t["categories"]:
            cat_to_topic[c] = t["id"]
    return tax, cat_to_topic, topic_meta


def slugify(s: str) -> str:
    s = s.lower()
    repl = {"á": "a", "à": "a", "ã": "a", "â": "a", "é": "e", "ê": "e", "í": "i",
            "ó": "o", "ô": "o", "õ": "o", "ú": "u", "ç": "c", "&": "and", "→": "to"}
    for k, v in repl.items():
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"


def norm_name(name: str) -> str:
    n = name.lower()
    n = re.sub(r"\s*\(.*?\)\s*", " ", n)        # drop parentheticals for matching
    n = re.sub(r"\s*[→/].*$", "", n)         # drop "X -> Y" / "X / Y" tails
    n = re.sub(r"[^a-z0-9]+", "", n)
    return n


def clean_url(tok: str) -> str | None:
    tok = tok.strip().strip("().,;:·•").strip()
    if not tok or tok.startswith("@"):
        return None
    low = tok.lower()
    m = DOMAIN_RE.match(low)
    if not m:
        return None
    host = m.group(2)
    if not TLD_OK.search(host):
        return None
    # reject false positives like "e.g" / single-letter TLDs already handled by TLD_OK
    if host.count(".") == 0:
        return None
    return tok if tok.startswith("http") else "https://" + tok


def host_of(url: str) -> str:
    h = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].lower()
    return h[4:] if h.startswith("www.") else h


def match_neighbourhood(area: str, tax) -> tuple[str | None, str | None]:
    a = area.lower()
    for nb in tax["neighbourhoods"]:
        for alias in nb["aliases"]:
            if alias in a:
                return nb["name"], nb["zone"]
    return None, None


def parse_segment(name: str, segment: str, tax):
    """Pull area, description, website, handles and socials out of one entry's text."""
    seg = segment.strip()
    seg = re.sub(r"^[—\-:\s]+", "", seg)  # leading em-dash/space

    # split the leading "area — description" off the link tail.
    # links usually follow the last em-dash or sit after a period; we grab them globally below.
    parts = [p.strip() for p in re.split(r"\s+[—]\s+", seg)]
    area = parts[0] if parts else ""
    # area shouldn't be a URL or contain a link separator
    if "http" in area or "·" in area or len(area) > 80:
        area = ""
    description = ""
    if len(parts) >= 2:
        description = parts[1]
    # strip trailing link/handle debris from area/description
    area = re.sub(r"\s*\(.*$", "", area).strip(" .,")

    websites, others, socials, handles = [], [], {}, []
    for tok in re.split(r"[\s]+", seg):
        u = clean_url(tok)
        if u:
            h = host_of(u)
            base = ".".join(h.split(".")[-2:]) if h.count(".") >= 1 else h
            sk = next((SOCIAL_HOSTS[k] for k in SOCIAL_HOSTS if h == k or h.endswith("." + k) or base == k), None)
            if sk:
                socials.setdefault(sk, u)
            else:
                websites.append(u)
    for m in HANDLE_RE.finditer(seg):
        hh = m.group(1).strip(".")
        if hh and hh.lower() not in ("gmail", "hotmail"):
            handles.append(hh)

    website = websites[0] if websites else None
    others = websites[1:]
    return area, description, website, others, socials, handles


def main():
    tax, cat_to_topic, topic_meta = load_taxonomy()
    text = DIRECTORY.read_text(encoding="utf-8")
    lines = text.splitlines()

    records: dict[str, dict] = {}
    order: list[str] = []
    current_cat = None

    for raw in lines:
        mh = CAT_HEAD_RE.match(raw)
        if mh:
            current_cat = int(mh.group(1))
            continue
        if current_cat is None:
            continue
        stripped = raw.lstrip()
        if not stripped.startswith("- "):
            continue
        content = stripped[2:]
        # un-bold our own [FLAG] markers so they aren't mistaken for venue names
        # and so the link/handle that follows a flag stays attached to its venue.
        content = re.sub(r"\*\*(\[[^\]]*\])\*\*", r"\1", content)
        bolds = list(BOLD_RE.finditer(content))
        if not bolds:
            continue

        flags = [f.strip() for f in FLAG_RE.findall(content)]
        joined = " ".join(flags).lower()
        line_status = "active"
        if "relocated" in joined:
            line_status = "relocated"
        if "not in 20" in joined or "reformatted" in joined:
            line_status = "not_running"
        if "closing" in joined:
            line_status = "closing"
        if "at risk" in joined:
            line_status = "at_risk"
        if "may have closed" in joined or "reported closed" in joined or "re-verify" in joined:
            line_status = "possibly_closed"
        if "renovation" in joined:
            line_status = "renovation"
        elif re.search(r"\[closed", content, re.I) and "may have" not in joined and "reported" not in joined:
            line_status = "closed"
        if "secured" in joined:
            line_status = "active"

        assigned_status = False
        for i, b in enumerate(bolds):
            name = b.group(1).strip().strip("*").strip()
            if not name or len(name) < 2:
                continue
            if name.startswith("["):       # a bolded [FLAG] marker, not a venue
                continue
            # status & flags belong to the first real entry on the line only
            this_status = line_status if not assigned_status else "active"
            this_flags = list(flags) if not assigned_status else []
            assigned_status = True
            seg_start = b.end()
            seg_end = bolds[i + 1].start() if i + 1 < len(bolds) else len(content)
            segment = content[seg_start:seg_end]

            is_xref = bool(re.search(r"see\s+§\s*\d", segment, re.I) or re.search(r"primary\s+§", segment, re.I))
            area, description, website, others, socials, handles = parse_segment(name, segment, tax)

            key = norm_name(name)
            topic = cat_to_topic.get(current_cat, "guides")
            host = host_of(website) if website else None
            base = ".".join(host.split(".")[-2:]) if host and host.count(".") >= 1 else host
            provider = None
            if host:
                provider = next((FEED_PROVIDERS[k] for k in FEED_PROVIDERS if host == k or host.endswith("." + k) or base == k), None)
                provider = provider or next((SOCIAL_HOSTS[k] for k in SOCIAL_HOSTS if host == k or host.endswith("." + k)), None)
                provider = provider or "generic"
            elif socials:
                provider = next(iter(socials))
            crawlable = bool(website) and provider not in ("instagram", "facebook", "telegram", "tiktok", "whatsapp")

            if key not in records:
                records[key] = {
                    "id": "", "name": name, "area": area, "neighbourhood": None, "zone": None,
                    "description": description, "website": website, "other_urls": list(others),
                    "instagram": socials.get("instagram"), "facebook": socials.get("facebook"),
                    "handles": list(dict.fromkeys(handles)),
                    "provider": provider, "crawlable": crawlable,
                    "topic": topic, "categories": [current_cat],
                    "primary_category": None if is_xref else current_cat,
                    "status": this_status, "flags": list(this_flags),
                }
                order.append(key)
            else:
                r = records[key]
                if current_cat not in r["categories"]:
                    r["categories"].append(current_cat)
                if not is_xref and r["primary_category"] is None:
                    r["primary_category"] = current_cat
                    r["topic"] = topic
                # fill gaps from a richer occurrence
                if not r["website"] and website:
                    r["website"] = website
                    r["provider"] = provider
                    r["crawlable"] = crawlable
                if not r["area"] and area:
                    r["area"] = area
                if not r["description"] and description:
                    r["description"] = description
                if not r["instagram"] and socials.get("instagram"):
                    r["instagram"] = socials["instagram"]
                if not r["facebook"] and socials.get("facebook"):
                    r["facebook"] = socials["facebook"]
                for h in handles:
                    if h not in r["handles"]:
                        r["handles"].append(h)
                for f in this_flags:
                    if f not in r["flags"]:
                        r["flags"].append(f)
                if this_status != "active" and r["status"] == "active":
                    r["status"] = this_status

    # finalise: ids, neighbourhoods, primary category fallback
    used_ids = set()
    out_records = []
    for key in order:
        r = records[key]
        if r["primary_category"] is None:
            r["primary_category"] = r["categories"][0]
            r["topic"] = cat_to_topic.get(r["primary_category"], "guides")
        nb, zone = match_neighbourhood(r["area"], tax)
        r["neighbourhood"], r["zone"] = nb, zone
        base_id = slugify(r["name"])
        sid = base_id
        n = 2
        while sid in used_ids:
            sid = f"{base_id}-{n}"
            n += 1
        used_ids.add(sid)
        r["id"] = sid
        out_records.append(r)

    # keep every venue in the registry (even link-less ones) for completeness;
    # the `crawlable` flag governs whether the weekly crawl actually fetches it.
    # Drop the handful of bold sub-labels I wrote (e.g. "Festivals tied to the scene:")
    # and anything with no real letters in the name.
    def is_label(r):
        n = r["name"].rstrip()
        return n.endswith(":") or not re.search(r"[A-Za-zÀ-ÿ]", n)
    cleaned = [r for r in out_records if not is_label(r)]

    payload = {
        "generated_from": "LISBON-EVENTS.md",
        "count": len(cleaned),
        "sources": cleaned,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- summary ----
    crawlable = [r for r in cleaned if r["crawlable"]]
    by_topic: dict[str, int] = {}
    by_status: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    for r in cleaned:
        by_topic[r["topic"]] = by_topic.get(r["topic"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_provider[r["provider"] or "none"] = by_provider.get(r["provider"] or "none", 0) + 1

    print(f"Parsed {len(cleaned)} unique sources -> {OUT.relative_to(ROOT)}")
    print(f"  crawlable (has non-social website): {len(crawlable)}")
    print("  by status: " + ", ".join(f"{k}={v}" for k, v in sorted(by_status.items())))
    print("  by topic:  " + ", ".join(f"{k}={v}" for k, v in sorted(by_topic.items(), key=lambda x: -x[1])))
    print("  top providers: " + ", ".join(f"{k}={v}" for k, v in sorted(by_provider.items(), key=lambda x: -x[1])[:10]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
