"""Final AI sanity/dedup pass over the assembled week (provider-agnostic).

The rule-based pipeline (collapse + venue aliases + dedupe) handles the cheap,
deterministic 95%. What it cannot do is *judge* whether two events with different
titles AND venues AND no shared coordinate are the same real event ("Designing
Sustainable Futures" at the organiser vs at the actual venue), or whether an
exhibition was mis-tagged. That semantic call is what an LLM is good at.

So AFTER dedupe, we:
  1. find the ambiguous *residue* deterministically — events the rules left
     separate but that look similar (shared distinctive title tokens or one title
     contained in the other, with overlapping dates). This is cheap and bounds
     what the model sees to a few dozen small clusters, not all ~800 events.
  2. ask the model, in ONE call, which members of each cluster are the same event
     (+ the canonical title/venue/topic + a confidence), and to flag obviously
     broken events.
  3. AUTO-APPLY high-confidence merges/topic-fixes; write the rest to
     proposed-changes for /admin review. Nothing low-confidence ships silently.

Reading the compact event list (no page fetching) is cheap; this is NOT the
per-page extraction that drove cost. Budget-gated by the CostTracker all the same.
A model/parse failure degrades to "no changes" — never crashes the merge.
"""
from __future__ import annotations

import hashlib

import core
import extract

# link two events into a candidate cluster when they could be the same event
_MIN_CONTAIN = 12          # title-containment length floor (chars, normalized)
_MIN_SHARED_TOKENS = 2     # distinctive title tokens in common
MAX_CLUSTERS = 40          # cap what the model sees (cost + focus)
MAX_CLUSTER_SIZE = 8       # a bigger "cluster" is a generic title, not a dup set


def _span(e: dict):
    s = (e.get("start") or "")[:10]
    return s, max(s, (e.get("end") or "")[:10])


def _overlap(a: dict, b: dict) -> bool:
    sa, ea = _span(a); sb, eb = _span(b)
    return bool(sa) and bool(sb) and sa <= eb and sb <= ea


def _linked(a: dict, b: dict) -> bool:
    """Heuristic: could a and b be the same real event? (over-includes on purpose
    — the model makes the final call)."""
    if not _overlap(a, b):
        return False
    na, nb = core._nt(a.get("title")), core._nt(b.get("title"))
    if min(len(na), len(nb)) >= _MIN_CONTAIN and (na in nb or nb in na):
        return True
    ta, tb = core._distinctive_tokens(a.get("title")), core._distinctive_tokens(b.get("title"))
    return len(ta & tb) >= _MIN_SHARED_TOKENS


def candidate_clusters(events: list[dict]) -> list[list[dict]]:
    """Union-find events into clusters of suspected same-event copies the rules
    left separate. Returns clusters of size 2..MAX_CLUSTER_SIZE, capped."""
    parent = list(range(len(events)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        parent[find(i)] = find(j)

    # bucket by distinctive token to avoid the O(n^2) all-pairs compare
    by_token: dict = {}
    for i, e in enumerate(events):
        for tok in core._distinctive_tokens(e.get("title")):
            by_token.setdefault(tok, []).append(i)
    for idxs in by_token.values():
        if len(idxs) > 60:        # a token shared by dozens of events is generic
            continue
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                if _linked(events[idxs[a]], events[idxs[b]]):
                    union(idxs[a], idxs[b])

    groups: dict = {}
    for i in range(len(events)):
        groups.setdefault(find(i), []).append(events[i])
    clusters = [g for g in groups.values() if 2 <= len(g) <= MAX_CLUSTER_SIZE]
    # biggest (most-duplicated) first, capped
    clusters.sort(key=len, reverse=True)
    return clusters[:MAX_CLUSTERS]


_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"clusters": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "cluster": {"type": "integer"},
            "merge": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "members": {"type": "array", "items": {"type": "integer"},
                                "description": "local indices that are the SAME event"},
                    "canonical": {"type": "integer", "description": "index with the clearest title/venue"},
                    "topic": {"type": "string", "description": "correct topic id for the merged event"},
                    "confidence": {"type": "number", "description": "0..1"},
                    "reason": {"type": "string"},
                }, "required": ["members", "canonical", "confidence"]}},
            "flags": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"index": {"type": "integer"}, "issue": {"type": "string"}},
                "required": ["index", "issue"]}},
        }, "required": ["cluster"]}}},
    "required": ["clusters"],
}

_HINT = ('{"clusters":[{"cluster":0,"merge":[{"members":[0,2],"canonical":2,"topic":"art",'
         '"confidence":0.95,"reason":"same exhibition, organiser vs venue"}],"flags":[]}]}')


def _system(topic_ids: list[str]) -> str:
    return (
        "És um verificador final de uma agenda de eventos de Lisboa. Recebes GRUPOS de "
        "eventos que um sistema de regras achou parecidos mas NÃO juntou. Para cada grupo, "
        "decide quais ENTRADAS são o MESMO evento real (mesma exposição/concerto/espetáculo, "
        "mesmo que o título ou o local estejam escritos de forma diferente, ou um seja o "
        "organizador e outro o local). Junta os índices em `members`, escolhe `canonical` "
        "(o título/local mais claro), indica o `topic` correto e uma `confidence` 0..1. "
        "NÃO juntes eventos genuinamente diferentes (dois concertos diferentes na mesma noite "
        "no mesmo sítio). Em `flags` assinala entradas obviamente partidas (título que é uma "
        "data, tema errado para uma exposição, local que é um placeholder). Só junta com "
        "confiança alta quando tiveres a certeza. topic ∈ {" + ", ".join(topic_ids) + "}.")


def _compact(e: dict) -> dict:
    return {"title": e.get("title"), "venue": e.get("venue"),
            "neigh": e.get("neighbourhood"), "start": (e.get("start") or "")[:10],
            "end": (e.get("end") or "")[:10], "topic": e.get("topic"), "source": e.get("source")}


def _cluster_sig(members: list[dict]) -> str:
    """Stable id for a merge decision so a revert survives re-crawls (event ids
    change each crawl, titles/venues don't). Sorted normalized title@venue."""
    key = "||".join(sorted(f"{core._nt(m.get('title'))}@{core._nt(m.get('venue') or '')}" for m in members))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def review(events, prov, client, cfg, tax, tracker, overrides=None):
    """Final AI judge over the ambiguous residue. Auto-applies EVERY merge/topic
    fix it returns — recording each reversibly — EXCEPT a cluster whose signature
    is in `overrides` (something you reverted before). Returns (events, changes,
    stats): `changes` is the reversible log for /admin (each merge carries the full
    removed events so the button can restore them). Any failure -> events unchanged."""
    overrides = overrides or set()
    stats = {"clusters": 0, "merged": 0, "topic_fixed": 0, "flagged": 0, "skipped": 0}
    clusters = candidate_clusters(events)
    stats["clusters"] = len(clusters)
    if not clusters or tracker.exhausted():
        return events, [], stats
    valid_topics = set(core.topic_ids(tax))
    payload = [{"cluster": ci, "events": [_compact(e) for e in cl]} for ci, cl in enumerate(clusters)]
    user = "Grupos a verificar (índices locais por grupo):\n" + extract.json.dumps(payload, ensure_ascii=False)
    data = extract.json_call(prov, client, cfg["ai"]["model_cheap"],
                             _system([t["id"] for t in tax["topics"] if not t.get("is_aggregator")]),
                             user, _SCHEMA, _HINT, 4000, tracker)
    if not data:
        return events, [], stats

    drop, changes = set(), []
    for verdict in (data.get("clusters") or []):
        ci = verdict.get("cluster")
        if not isinstance(ci, int) or not (0 <= ci < len(clusters)):
            continue
        cl = clusters[ci]
        for grp in (verdict.get("merge") or []):
            members = [cl[i] for i in (grp.get("members") or [])
                       if isinstance(i, int) and 0 <= i < len(cl) and cl[i]["id"] not in drop]
            if len(members) < 2:
                continue
            sig = _cluster_sig(members)
            if sig in overrides:          # you reverted this before — leave it alone
                stats["skipped"] += 1
                continue
            ci_idx = grp.get("canonical")
            canon = (cl[ci_idx] if isinstance(ci_idx, int) and 0 <= ci_idx < len(cl)
                     and cl[ci_idx]["id"] not in drop else members[0])
            removed = [dict(m) for m in members if m["id"] != canon["id"]]
            if not removed:
                continue
            conf = round(float(grp.get("confidence") or 0), 2)
            topic = grp.get("topic") if grp.get("topic") in valid_topics else None
            old_topic = canon.get("topic")
            _merge_into(canon, [m for m in members if m["id"] != canon["id"]], topic, drop)
            stats["merged"] += len(removed)
            if topic and topic != old_topic:
                stats["topic_fixed"] += 1
            changes.append({"kind": "merge", "sig": sig, "confidence": conf, "reason": grp.get("reason", ""),
                            "canonical": {"id": canon["id"], "title": canon["title"], "venue": canon.get("venue")},
                            "removed": removed,
                            "topic": {"from": old_topic, "to": topic} if (topic and topic != old_topic) else None})
        for fl in (verdict.get("flags") or []):
            i = fl.get("index")
            if isinstance(i, int) and 0 <= i < len(cl):
                changes.append({"kind": "flag", "issue": fl.get("issue", ""),
                                "event": {"id": cl[i]["id"], "title": cl[i]["title"], "venue": cl[i].get("venue")}})
                stats["flagged"] += 1

    kept = [e for e in events if e["id"] not in drop]
    return kept, changes, stats


def _merge_into(canon: dict, others: list[dict], topic: str | None, drop: set) -> None:
    """Fold `others` into `canon`: widen the run, fill missing image/price/url/
    coords/neighbourhood, adopt the chosen topic. Mark others dropped."""
    if topic:
        canon["topic"] = topic
    for o in others:
        drop.add(o["id"])
        if (o.get("end") or "") > (canon.get("end") or ""):
            canon["end"] = o["end"]
        canon["ongoing"] = bool(canon.get("ongoing") or o.get("ongoing"))
        canon["days"] = sorted(set(canon.get("days") or []) | set(o.get("days") or []),
                               key=core.DAYS.index) if (canon.get("days") or o.get("days")) else canon.get("days")
        for f in ("image", "price", "neighbourhood", "lat", "lng"):
            if not canon.get(f) and o.get(f):
                canon[f] = o[f]
        if not (canon.get("price") or {}).get("text") and (o.get("price") or {}).get("text"):
            canon["price"] = o["price"]
