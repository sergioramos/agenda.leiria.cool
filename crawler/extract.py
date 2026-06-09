"""
AI extraction for the Pregoeiro crawler.

Sends trimmed page text to Claude Haiku 4.5 (cheap) and asks for the next-week
events as structured JSON. Escalates to Sonnet 4.6 only when the cheap model
returns nothing useful and the budget allows. Tracks real token spend against a
hard per-run USD cap; once the cap is hit, extraction is skipped (feeds still run).

Models and prices are the user's explicit choice (cost-sensitive crawl), per
the claude-api reference: haiku-4-5 $1/$5, sonnet-4-6 $3/$15 per MTok.
"""
from __future__ import annotations
import json

import core

# USD per 1M tokens: input, output, cache-read (~0.1x in), cache-write (~1.25x in)
PRICES = {
    "claude-haiku-4-5":  {"in": 1.0, "out": 5.0, "cr": 0.10, "cw": 1.25},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0, "cr": 0.30, "cw": 3.75},
}


class CostTracker:
    def __init__(self, cap_usd: float):
        self.cap = cap_usd
        self.spent = 0.0
        self.calls = 0

    def add(self, model: str, usage) -> None:
        p = PRICES.get(model) or PRICES["claude-haiku-4-5"]
        self.spent += (
            (getattr(usage, "input_tokens", 0) or 0) * p["in"]
            + (getattr(usage, "output_tokens", 0) or 0) * p["out"]
            + (getattr(usage, "cache_read_input_tokens", 0) or 0) * p["cr"]
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0) * p["cw"]
        ) / 1_000_000
        self.calls += 1

    def exhausted(self) -> bool:
        return self.spent >= self.cap


def _schema(topic_ids: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "date": {"type": "string", "description": "Event date or start, ISO YYYY-MM-DD (add THH:MM if a time is given)"},
                        "end_date": {"type": "string", "description": "ISO end date if it's a run/multi-day event, else empty"},
                        "is_free": {"type": "boolean"},
                        "price_text": {"type": "string", "description": "e.g. €15, or empty if unknown"},
                        "topic": {"type": "string", "enum": topic_ids},
                        "language": {"type": "string", "enum": ["pt", "en", "pt/en"]},
                        "url": {"type": "string"},
                        "description": {"type": "string", "description": "one short line, in Portuguese"},
                    },
                    "required": ["title", "date", "topic", "is_free"],
                },
            }
        },
        "required": ["events"],
    }


def _system_prompt(mon, window_end, tax) -> str:
    topics = "; ".join(f'{t["id"]} = {t["label"]}' for t in tax["topics"] if not t.get("is_aggregator"))
    return (
        "És um extractor de eventos de Lisboa. A partir do texto de uma página, devolve APENAS os "
        f"eventos com data entre {mon.isoformat()} e {window_end.isoformat()} (inclusive), mais "
        "exposições/temporadas já a decorrer que ainda estejam abertas nesse período. "
        "Ignora itens sem data concreta, menus, publicidade e navegação. Datas em ISO. "
        "Se o preço não for claro, is_free=false e price_text vazio. Descrição: uma linha curta em português. "
        f"Escolhe o tema mais adequado de: {topics}. Se não houver eventos, devolve events: []."
    )


def extract(client, source: dict, page_text: str, mon, window_end, cfg: dict, tax: dict,
            tracker: CostTracker) -> list[dict]:
    if tracker.exhausted() or not page_text:
        return []
    import anthropic  # local import so --no-ai never needs the package
    tids = [t["id"] for t in tax["topics"] if not t.get("is_aggregator")]
    system = [{"type": "text", "text": _system_prompt(mon, window_end, tax),
               "cache_control": {"type": "ephemeral"}}]
    user = f"Local/fonte: {source['name']} ({source.get('website')})\n\nTexto da página:\n{page_text}"
    schema = _schema(tids)

    def call(model):
        try:
            resp = client.messages.create(
                model=model, max_tokens=6000, system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
            tracker.add(model, resp.usage)
            raw = next((b.text for b in resp.content if b.type == "text"), "")
            return json.loads(raw).get("events", [])
        except (anthropic.APIError, json.JSONDecodeError, StopIteration, Exception):
            return None

    items = call(cfg["ai"]["model_cheap"])
    if (not items) and cfg["ai"].get("escalate_on_low_confidence") and not tracker.exhausted():
        strong = call(cfg["ai"]["model_strong"])
        if strong is not None:
            items = strong
    if not items:
        return []

    out = []
    valid_topics = core.topic_ids(tax)
    for it in items:
        parsed = core.parse_dt(it.get("date"))
        if not parsed:
            continue
        start_d, has_time, start_iso = parsed
        end_parsed = core.parse_dt(it.get("end_date"))
        end_d = end_parsed[0] if end_parsed else None
        topic = it.get("topic") if it.get("topic") in valid_topics else (source.get("topic") or "guides")
        price_text = it.get("price_text") or ""
        price = ({"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
                 if it.get("is_free") else core.detect_price(price_text or "x"))
        lang = it.get("language") or "pt"
        ev = core.make_event(
            title=it.get("title"), source=source, topic=topic, mon=mon, window_end=window_end,
            start_d=start_d, end_d=end_d, has_time=has_time, start_iso=start_iso,
            price=price, url=it.get("url"), description=it.get("description"),
            language=(["pt", "en"] if lang == "pt/en" else [lang]),
            categories=source.get("categories", [])[:2],
        )
        if ev:
            out.append(ev)
    return out
