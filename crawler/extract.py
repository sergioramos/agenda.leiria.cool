"""
AI extraction for the Pregoeiro crawler — provider-agnostic.

The model only reads page text and returns events as JSON; the crawler does all
fetching, so no web-search/tools are needed from the model. Provider is a config
switch (config.yaml -> ai.provider):

  deepseek   -> OpenAI-compatible API (api.deepseek.com), JSON mode. Default.
               deepseek-v4-pro: ~$0.435/$0.87 per MTok. deepseek-v4-flash: ~$0.14/$0.28.
  anthropic  -> Anthropic SDK, strict json_schema structured outputs.
               claude-haiku-4-5: $1/$5. claude-sonnet-4-6: $3/$15.

A hard per-run USD cap (split across shards) stops AI calls once reached; feeds
still run. Any API/parse error degrades to [] — never crashes the shard.
"""
from __future__ import annotations
import json
import os
import re

import core

# USD per 1M tokens. in=input(miss), out=output, cr=cache-read/hit, cw=cache-write premium.
PRICES = {
    "deepseek-v4-pro":   {"in": 0.435, "out": 0.87, "cr": 0.003625, "cw": 0.435},
    "deepseek-v4-flash": {"in": 0.14,  "out": 0.28, "cr": 0.0028,   "cw": 0.14},
    "claude-haiku-4-5":  {"in": 1.0,   "out": 5.0,  "cr": 0.10,     "cw": 1.25},
    "claude-sonnet-4-6": {"in": 3.0,   "out": 15.0, "cr": 0.30,     "cw": 3.75},
}

EVENT_HINT = ('{"events":[{"title":"...","date":"YYYY-MM-DD","end_date":"",'
              '"is_free":true,"price_text":"","topic":"music","language":"pt",'
              '"venue":"","url":"","description":"..."}]}')


class CostTracker:
    def __init__(self, cap_usd: float):
        self.cap = cap_usd
        self.spent = 0.0
        self.calls = 0

    def add(self, model: str, in_tok: int, out_tok: int, cr_tok: int = 0, cw_tok: int = 0) -> None:
        # unpriced model → charge at the worst-case rate so the cap triggers conservatively
        p = PRICES.get(model) or max(PRICES.values(), key=lambda r: r["out"])
        self.spent += (in_tok * p["in"] + out_tok * p["out"]
                       + cr_tok * p["cr"] + cw_tok * p["cw"]) / 1_000_000
        self.calls += 1

    def exhausted(self) -> bool:
        return self.spent >= self.cap


def get_client(cfg: dict):
    """Return (provider, client). Raises if the SDK or key is missing."""
    prov = cfg["ai"].get("provider", "deepseek")
    base = cfg["ai"].get("base_url") or None
    if prov == "anthropic":
        import anthropic
        return prov, anthropic.Anthropic(**({"base_url": base} if base else {}))
    from openai import OpenAI  # deepseek + any OpenAI-compatible host
    key = os.environ.get(cfg["ai"].get("api_key_env", "DEEPSEEK_API_KEY"), "")
    return prov, OpenAI(base_url=base or "https://api.deepseek.com", api_key=key)


def json_call(prov, client, model, system_text, user_text, schema, schema_hint,
              max_tokens, tracker: CostTracker):
    """One model call that returns parsed JSON (dict) or None on any failure."""
    if tracker.exhausted():
        return None
    try:
        if prov == "anthropic":
            resp = client.messages.create(
                model=model, max_tokens=max_tokens,
                system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user_text}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
            u = resp.usage
            tracker.add(model, getattr(u, "input_tokens", 0) or 0, getattr(u, "output_tokens", 0) or 0,
                        getattr(u, "cache_read_input_tokens", 0) or 0,
                        getattr(u, "cache_creation_input_tokens", 0) or 0)
            raw = next((b.text for b in resp.content if b.type == "text"), "")
            return json.loads(raw)
        # OpenAI-compatible (DeepSeek): JSON mode needs the word "json" + an example in the prompt
        sys_text = (system_text + "\n\nResponde APENAS com JSON válido (sem markdown, sem texto à volta), "
                    "exatamente neste formato:\n" + schema_hint)
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": sys_text}, {"role": "user", "content": user_text}],
            response_format={"type": "json_object"},
        )
        u = resp.usage
        tracker.add(model, getattr(u, "prompt_tokens", 0) or 0, getattr(u, "completion_tokens", 0) or 0)
        return json.loads(resp.choices[0].message.content)
    except Exception:
        return None


def _schema(topic_ids: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"events": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "title": {"type": "string"},
                "date": {"type": "string", "description": "Event date/start, ISO YYYY-MM-DD (add THH:MM if a time is given)"},
                "end_date": {"type": "string", "description": "ISO end date for runs/multi-day, else empty"},
                "is_free": {"type": "boolean"},
                "price_text": {"type": "string"},
                "topic": {"type": "string", "enum": topic_ids},
                "language": {"type": "string", "enum": ["pt", "en", "pt/en"]},
                "venue": {"type": "string", "description": "Nome do local onde o evento decorre, se a página o indicar"},
                "url": {"type": "string", "description": "URL da página própria do evento, copiado do texto (aparece entre parênteses retos); vazio se não existir"},
                "description": {"type": "string", "description": "one short line, in Portuguese"},
            },
            "required": ["title", "date", "topic", "is_free"],
        }}},
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
        "Os links da página aparecem no texto entre parênteses retos, ex.: [https://…]. Quando um evento "
        "tiver página própria, copia esse URL exatamente para o campo url (sem os parênteses retos); NUNCA "
        "inventes URLs — se não houver link no texto, deixa url vazio. Preenche o campo venue com o nome do "
        "local onde o evento decorre quando a página o indicar (essencial em páginas de agenda que listam "
        "vários locais). Mantém as descrições muito curtas. Se a página tiver mais de 60 eventos no período, "
        "devolve apenas os 60 primeiros. "
        f"Escolhe o tema (campo topic) mais adequado de: {topics}. Se não houver eventos, devolve events: []."
    )


def _checked_url(raw, source: dict, page_links: set | None) -> str | None:
    """Accept a model-returned URL only if it is a link that was actually on the
    page, or at least lives on the source's own site — a fabricated external
    URL is worse than falling back to the venue homepage."""
    u = core.resolve_url(raw, source.get("website"))
    if not u:
        return None
    if page_links and u in page_links:
        return u
    src_host = core.site_key(source.get("website") or "").split("/")[0]
    if src_host and core.site_key(u).split("/")[0] == src_host:
        return u
    return None


def extract(prov, client, source: dict, page_text: str, mon, window_end, cfg: dict, tax: dict,
            tracker: CostTracker, venues_idx: dict | None = None,
            page_links: set | None = None) -> list[dict]:
    if tracker.exhausted() or not page_text:
        return []
    tids = [t["id"] for t in tax["topics"] if not t.get("is_aggregator")]
    system = _system_prompt(mon, window_end, tax)
    user = f"Local/fonte: {source['name']} ({source.get('website')})\n\nTexto da página:\n{page_text}"
    schema = _schema(tids)

    # 8000 output tokens ≈ 60+ events; dense agenda pages truncated JSON-mode
    # responses parse as nothing, so capacity matters more than cost here
    data = json_call(prov, client, cfg["ai"]["model_cheap"], system, user, schema, EVENT_HINT, 8000, tracker)
    items = (data or {}).get("events")
    if not items and cfg["ai"].get("escalate_on_low_confidence") and not tracker.exhausted() \
            and cfg["ai"]["model_strong"] != cfg["ai"]["model_cheap"]:
        data = json_call(prov, client, cfg["ai"]["model_strong"], system, user, schema, EVENT_HINT, 8000, tracker)
        items = (data or {}).get("events")
    if not items:
        return []

    out, valid = [], core.topic_ids(tax)
    for it in items:
        if not isinstance(it, dict):
            continue
        parsed = core.parse_dt(it.get("date"))
        if not parsed:
            continue
        start_d, has_time, start_iso = parsed
        end_parsed = core.parse_dt(it.get("end_date"))
        topic = it.get("topic") if it.get("topic") in valid else (source.get("topic") or "guides")
        price = ({"is_free": True, "min": 0, "currency": "EUR", "text": "Grátis"}
                 if it.get("is_free") else core.detect_price(it.get("price_text") or "x"))
        lang = it.get("language") or "pt"
        # map the extracted venue name back to the seed list (canonical name +
        # neighbourhood); a name we don't know is only trusted on agenda pages
        venue_name = neigh = zone = None
        vraw = re.sub(r"\s+", " ", str(it.get("venue") or "")).strip()[:120]
        if vraw and core._nt(vraw) != core._nt(source["name"]):
            known = core.resolve_venue(vraw, venues_idx or {})
            if known is not None:
                venue_name = known["name"]
                neigh, zone = known.get("neighbourhood"), known.get("zone")
            elif source.get("topic") == "guides":
                venue_name = vraw
        ev = core.make_event(
            title=it.get("title"), source=source, topic=topic, mon=mon, window_end=window_end,
            start_d=start_d, end_d=(end_parsed[0] if end_parsed else None), has_time=has_time,
            start_iso=start_iso, price=price, url=_checked_url(it.get("url"), source, page_links),
            description=it.get("description"),
            language=(["pt", "en"] if lang == "pt/en" else [lang]),
            categories=source.get("categories", [])[:2],
            venue_name=venue_name, neighbourhood=neigh, zone=zone,
        )
        if ev:
            out.append(ev)
    return out
