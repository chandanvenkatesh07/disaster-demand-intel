"""Home Depot store locations inside Florida, from OpenStreetMap (Overpass API).

Queries Overpass for any element brand-tagged "The Home Depot" within the
Florida administrative boundary. Persists store points to DuckDB and computes
per-county store density.

OSM is community-maintained; coverage is good but not exhaustive. Treat counts
as a *floor*. Re-run to refresh.
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb
import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "processed" / "regions.duckdb"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
]

# Tags Overpass / OSM uses for Home Depot. We match on either the canonical
# brand wikidata id (Q864407) or the brand string. nwr = nodes+ways+relations.
OVERPASS_QUERY = """
[out:json][timeout:60];
area["ISO3166-2"="US-FL"][admin_level=4]->.fl;
(
  nwr["brand:wikidata"="Q864407"](area.fl);
  nwr["brand"="The Home Depot"](area.fl);
  nwr["brand"="Home Depot"](area.fl);
);
out center tags;
""".strip()


def _query_overpass() -> dict:
    last_err: Exception | None = None
    for url in OVERPASS_ENDPOINTS:
        try:
            with httpx.Client(timeout=90) as c:
                r = c.post(url, content=OVERPASS_QUERY,
                           headers={"Content-Type": "text/plain"})
                if r.status_code == 200:
                    return r.json()
                last_err = RuntimeError(f"{url} -> HTTP {r.status_code}")
        except httpx.HTTPError as e:
            last_err = e
        time.sleep(2)
    raise RuntimeError(f"All Overpass endpoints failed; last error: {last_err}")


def _extract_stores(payload: dict) -> list[dict]:
    out: list[dict] = []
    for el in payload.get("elements", []):
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        out.append({
            "osm_id": f"{el['type']}/{el['id']}",
            "lat": float(lat),
            "lon": float(lon),
            "name": tags.get("name", "Home Depot"),
            "addr_street": tags.get("addr:street"),
            "addr_city": tags.get("addr:city"),
            "addr_postcode": tags.get("addr:postcode"),
        })
    # OSM occasionally has dupes; de-dupe on rounded coords.
    seen: set[tuple[float, float]] = set()
    deduped: list[dict] = []
    for s in out:
        key = (round(s["lat"], 4), round(s["lon"], 4))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return deduped


def ingest() -> dict:
    payload = _query_overpass()
    stores = _extract_stores(payload)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(DB_PATH.as_posix())
    con.execute("""
        CREATE TABLE IF NOT EXISTS home_depot_store (
            osm_id TEXT PRIMARY KEY,
            lat DOUBLE NOT NULL,
            lon DOUBLE NOT NULL,
            name TEXT,
            addr_street TEXT,
            addr_city TEXT,
            addr_postcode TEXT
        )
    """)
    con.execute("DELETE FROM home_depot_store")
    con.executemany(
        """INSERT INTO home_depot_store VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(s["osm_id"], s["lat"], s["lon"], s["name"],
          s["addr_street"], s["addr_city"], s["addr_postcode"]) for s in stores],
    )
    con.close()
    return {"stores_written": len(stores)}


if __name__ == "__main__":
    print(ingest())
