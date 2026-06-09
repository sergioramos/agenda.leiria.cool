#!/usr/bin/env python3
"""
Generate ONE sample week of events so the website can be previewed before the
first real Sunday crawl runs. Output is clearly marked is_sample=true and the
site shows a banner saying so — nothing here is a real listing.

It also defines the canonical weekly-event schema that the real crawler
(crawl_events.py) will emit, so the site never needs to change.

Run:  py crawler/make_sample_week.py
Writes into docs/data/ (what GitHub Pages publishes).
"""
from __future__ import annotations
import json
import hashlib
import random
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "sources" / "sources.json"
TAX = ROOT / "sources" / "taxonomy.json"
DOCS = ROOT / "docs"
DATA = DOCS / "data"
WEEKS = DATA / "weeks"
PROPOSED = DATA / "proposed-changes"

random.seed(20260608)  # deterministic sample

# ---- WEEKLY EVENT SCHEMA (also produced by the real crawler) -----------------
# {
#   "week_start": "YYYY-MM-DD", "week_end": "YYYY-MM-DD",
#   "generated_at": ISO8601, "is_sample": bool,
#   "source_count": int, "event_count": int,
#   "events": [ {
#       "id": str, "title": str, "topic": <taxonomy id>, "categories": [int],
#       "venue": str, "source_id": str, "neighbourhood": str|None, "zone": str|None,
#       "start": ISO local (date or datetime), "end": ISO|None, "all_day": bool,
#       "ongoing": bool, "days": ["mon".."sun"],
#       "price": {"is_free": bool, "min": float|None, "currency": "EUR", "text": str},
#       "language": [str], "url": str|None, "source": str, "description": str,
#       "image": str|None
#   } ]
# }
# ------------------------------------------------------------------------------

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

TITLES = {
    "music":       ["Quarteto de jazz ao vivo", "Showcase indie", "Noite de fado", "Concerto sinfónico", "Sessão de cantautor"],
    "nightlife":   ["DJs residentes: house e disco", "Noite de techno até de manhã", "Kizomba e Afrobeat", "Rave queer", "DJ ao pôr do sol no rooftop"],
    "film":        ["Cinema ao ar livre: um clássico", "Retrospetiva de autor", "Estreia de documentário", "Filme de culto à meia-noite"],
    "art":         ["Inauguração / vernissage", "Exposição coletiva de pintura", "Exposição de fotografia", "Abertura noturna da galeria"],
    "performance": ["Dança contemporânea", "Estreia de teatro", "Espetáculo de teatro físico", "Espetáculo de marionetas"],
    "comedy":      ["Stand-up em inglês", "Open-mic de comédia", "Noite de improviso", "Noite de storytelling"],
    "workshops":   ["Aula de cerâmica na roda", "Workshop de serigrafia", "Prova de vinhos naturais", "Sessão de desenho de modelo"],
    "food":        ["Prova de vinhos naturais", "Supper club: menu surpresa", "Noite de queijos e enchidos", "Mercado de produtores"],
    "outdoors":    ["Corrida ao pôr do sol e convívio", "Dia de festival ao ar livre", "Caminhada guiada", "Sessão no quiosque junto ao rio"],
    "community":   ["Concerto + noite de DJ", "Repair café", "Mercado de bairro", "Festa de angariação"],
    "learning":    ["Conversa com autor e lançamento de livro", "Meetup de tecnologia + demos", "Conferência aberta", "Noite da comunidade de design"],
    "family":      ["Teatro para crianças (matinée)", "Workshop de ciência em família", "Espetáculo de marionetas para crianças"],
    "wellness":    ["Ecstatic dance", "Banho de som e respiração", "Cerimónia de cacau"],
    "social":      ["Noite de jogos de tabuleiro", "Milonga de tango", "Baile de swing", "Jantar com desconhecidos"],
    "tours":       ["Visita à arte urbana", "Passeio de fado e comida", "Experiência imersiva", "Prova de ginjinha a pé"],
}


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def make_event(src, topic, week_start, idx):
    title = random.choice(TITLES.get(topic, ["Event"]))
    ongoing = topic in ("art",) and random.random() < 0.5
    if ongoing:
        days = list(DAYS)
        start_d = week_start
        time = ""
        all_day = True
    else:
        n = random.choice([1, 1, 1, 2])
        day_idxs = sorted(random.sample(range(7), n))
        days = [DAYS[i] for i in day_idxs]
        start_d = week_start + timedelta(days=day_idxs[0])
        hour = {"nightlife": 23, "music": 21, "film": 21, "comedy": 21, "food": 19,
                "social": 20, "performance": 20, "wellness": 19}.get(topic, 18)
        time = f"{hour:02d}:00"
        all_day = False
    is_free = random.random() < 0.4
    if is_free:
        price = {"is_free": True, "min": 0, "currency": "EUR", "text": "Free"}
    else:
        amt = random.choice([5, 8, 10, 12, 15, 20, 25])
        price = {"is_free": False, "min": amt, "currency": "EUR", "text": f"€{amt}"}
    start_iso = start_d.isoformat() + (f"T{time}:00" if time else "")
    raw_id = f"{src['id']}|{title}|{start_iso}|{idx}"
    eid = hashlib.sha1(raw_id.encode()).hexdigest()[:12]
    return {
        "id": eid, "title": title, "topic": topic, "categories": src["categories"][:2],
        "venue": src["name"], "source_id": src["id"],
        "neighbourhood": src["neighbourhood"], "zone": src["zone"],
        "start": start_iso, "end": None, "all_day": all_day,
        "ongoing": ongoing, "days": days, "price": price,
        "language": ["en", "pt"] if random.random() < 0.3 else ["pt"],
        "url": src["website"], "source": "sample",
        "description": f"{title} — {src['name']}.",
        "image": None,
    }


def main():
    sources = load(SRC)["sources"]
    pool = [s for s in sources if s["crawlable"] and s["status"] in ("active", "renovation")
            and s["neighbourhood"] and s["topic"] != "guides"]
    by_topic: dict[str, list] = {}
    for s in pool:
        by_topic.setdefault(s["topic"], []).append(s)

    ws = monday_of(date(2026, 6, 9))
    we = ws + timedelta(days=6)
    events = []
    idx = 0
    for topic, venues in by_topic.items():
        random.shuffle(venues)
        for s in venues[:4]:           # a few sample events per topic
            events.append(make_event(s, topic, ws, idx)); idx += 1
    events.sort(key=lambda e: (e["start"], e["topic"]))

    week = {
        "week_start": ws.isoformat(), "week_end": we.isoformat(),
        "generated_at": datetime(2026, 6, 7, 6, 0, 0).isoformat() + "Z",
        "is_sample": True, "source_count": sum(1 for s in sources if s["crawlable"]),
        "event_count": len(events), "events": events,
    }

    WEEKS.mkdir(parents=True, exist_ok=True)
    PROPOSED.mkdir(parents=True, exist_ok=True)
    (WEEKS / f"{ws.isoformat()}.json").write_text(json.dumps(week, ensure_ascii=False, indent=2), encoding="utf-8")

    index = {"weeks": [{
        "start": ws.isoformat(), "end": we.isoformat(), "file": f"{ws.isoformat()}.json",
        "event_count": len(events), "is_sample": True, "generated_at": week["generated_at"],
    }]}
    (WEEKS / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    # sample "proposed changes" for the Monday review page
    closures = [s for s in sources if s["status"] in ("closed", "closing", "at_risk", "possibly_closed")][:6]
    proposed = {
        "generated_at": datetime(2026, 6, 8, 6, 0, 0).isoformat() + "Z",
        "is_sample": True,
        "closures": [{"id": s["id"], "name": s["name"], "neighbourhood": s["neighbourhood"],
                      "reason": (s["flags"][0] if s["flags"] else "no recent activity detected"),
                      "current_status": s["status"]} for s in closures],
        "new_venues": [
            {"name": "Sample New Venue", "neighbourhood": "Marvila", "topic": "nightlife",
             "url": "https://example.com", "found_via": "Resident Advisor", "note": "appeared in listings this week"},
        ],
    }
    (PROPOSED / "latest.json").write_text(json.dumps(proposed, ensure_ascii=False, indent=2), encoding="utf-8")

    # publish taxonomy alongside the site
    shutil.copyfile(TAX, DOCS / "taxonomy.json")

    print(f"Sample week {ws}..{we}: {len(events)} events across {len(by_topic)} topics.")
    print(f"  wrote {WEEKS / (ws.isoformat() + '.json')}")
    print(f"  wrote {WEEKS / 'index.json'}, {PROPOSED / 'latest.json'}, {DOCS / 'taxonomy.json'}")


if __name__ == "__main__":
    main()
