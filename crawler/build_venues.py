#!/usr/bin/env python3
"""
Build the venue directory sources/venues.json — the coordinate + neighbourhood
spine for the whole crawler. Seeded from AgendaLX's /venues endpoint (1800+ Lisbon
venues with lat/lng + address), with the neighbourhood derived ONCE per venue:
  1. alias match on (venue name + address)  -> finest (taxonomy aliases)
  2. point-in-polygon coords vs the freguesia GeoJSON -> parish -> neighbourhood
  3. else null
Coords outside greater Lisbon are dropped (AgendaLX has a few bad geocodes).

  py crawler/build_venues.py                         # fetch live + use shipped GeoJSON
  py crawler/build_venues.py --venues-json raw.json  # reuse a saved /venues dump
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core

AGENDALX_VENUES = "https://www.agendalx.pt/wp-json/agendalx/v1/venues"
GEOJSON_PATH = core.ROOT / "sources" / "lisboa-freguesias.geojson"


def _first(v):
    return (v[0] if isinstance(v, list) and v else v) or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venues-json", default=None, help="reuse a saved AgendaLX /venues dump")
    ap.add_argument("--geojson", default=str(GEOJSON_PATH))
    ap.add_argument("--name-prop", default=None, help="GeoJSON parish-name property (auto-detected if omitted)")
    args = ap.parse_args()

    tax = core.load_taxonomy()
    alias_idx = core._alias_index(tax)

    geojson, name_prop = {}, args.name_prop
    gp = Path(args.geojson)
    if gp.exists():
        geojson = json.loads(gp.read_text(encoding="utf-8"))
        if not name_prop:
            name_prop = _detect_name_prop(geojson)
        print(f"[geojson] {len(geojson.get('features', []))} features, name property = {name_prop!r}")
    else:
        print(f"[geojson] {gp} not found — neighbourhoods from aliases only")

    if args.venues_json:
        raw = json.loads(Path(args.venues_json).read_text(encoding="utf-8"))
    else:
        cfg = core.load_config()
        got = core.fetch(core.make_session(cfg), AGENDALX_VENUES, cfg)
        raw = json.loads(got[2]) if got and got[0] == 200 else []
    print(f"[venues] {len(raw)} raw AgendaLX venues")

    venues: dict = {}
    n_coords = n_alias = n_parish = 0
    for v in raw:
        name = (v.get("name") or "").strip()
        if not name or name.startswith("."):
            continue
        key = core._nt(name)
        if len(key) < 4 or key in venues:
            continue
        m = v.get("meta") or {}
        lat, lng = _first(m.get("_lat")), _first(m.get("_lng"))
        address = _first(m.get("_address"))
        good = core.valid_lisbon_coord(lat, lng)
        latf = float(lat) if good else None
        lngf = float(lng) if good else None
        if good:
            n_coords += 1

        neigh, zone = core.alias_neighbourhood(f"{name} {address}", alias_idx)
        if neigh:
            n_alias += 1
        elif good and geojson:
            neigh, zone = core.parish_neighbourhood(latf, lngf, geojson, name_prop)
            if neigh:
                n_parish += 1

        venues[key] = {"name": name[:120], "lat": latf, "lng": lngf,
                       "neighbourhood": neigh, "zone": zone, "address": address[:120] or None}

    out = {"count": len(venues),
           "meta": {"with_coords": n_coords, "neigh_from_alias": n_alias, "neigh_from_parish": n_parish},
           "venues": venues}
    (core.ROOT / "sources" / "venues.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    placed = sum(1 for v in venues.values() if v["neighbourhood"])
    print(f"[done] {len(venues)} venues -> sources/venues.json "
          f"({n_coords} with coords, {placed} with neighbourhood: {n_alias} alias + {n_parish} parish)")


def _detect_name_prop(geojson: dict) -> str:
    """Pick the property whose values look like freguesia names."""
    feats = geojson.get("features", [])
    if not feats:
        return "name"
    props = (feats[0].get("properties") or {})
    for cand in ("Freguesia", "freguesia", "NOME", "Nome", "nome", "name", "NAME",
                 "FREGUESIA", "Designacao", "DESIGNACAO"):
        if cand in props:
            return cand
    # fall back to the first string property
    for k, v in props.items():
        if isinstance(v, str):
            return k
    return "name"


if __name__ == "__main__":
    main()
