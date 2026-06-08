"""Fetch Florida county polygons from Census TIGERweb and stash them.

Stored two ways:
  - data/raw/fl_counties.geojson  (raw GeoJSON for the choropleth layer)
  - DuckDB table county_geom (fips, name, geometry as WKT) for point-in-poly
    joins via DuckDB Spatial.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_PATH = PROJECT_ROOT / "data" / "raw" / "fl_counties.geojson"
DB_PATH = PROJECT_ROOT / "data" / "processed" / "regions.duckdb"

TIGERWEB_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
    "State_County/MapServer/13/query"
    "?where=STATE%3D%2712%27&outFields=GEOID,NAME&f=geojson&outSR=4326"
)


def fetch_and_store() -> dict:
    with httpx.Client(timeout=60) as c:
        r = c.get(TIGERWEB_URL)
        r.raise_for_status()
        geojson = r.json()

    RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(json.dumps(geojson))

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(DB_PATH.as_posix())
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("""
        CREATE TABLE IF NOT EXISTS county_geom (
            fips TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            geom_wkt TEXT NOT NULL
        )
    """)
    con.execute("DELETE FROM county_geom")
    rows = []
    for feat in geojson.get("features", []):
        props = feat["properties"]
        fips = props["GEOID"]
        name = props["NAME"]
        # Convert GeoJSON geometry to WKT via DuckDB Spatial so it round-trips.
        wkt = con.execute(
            "SELECT ST_AsText(ST_GeomFromGeoJSON(?))",
            [json.dumps(feat["geometry"])],
        ).fetchone()[0]
        rows.append((fips, name, wkt))
    con.executemany("INSERT INTO county_geom VALUES (?, ?, ?)", rows)
    con.close()
    return {"counties_written": len(rows), "geojson_bytes": RAW_PATH.stat().st_size}


if __name__ == "__main__":
    print(fetch_and_store())
