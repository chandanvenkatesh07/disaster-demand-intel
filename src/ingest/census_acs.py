"""Census ACS 5-year ingestion for Florida counties.

Pulls the variables needed for the demand score:
  B01003_001  total population
  B25001_001  total housing units
  B25003_001  total occupied units (denominator for ownership rate)
  B25003_002  owner-occupied units
  B25035_001  median year structure built (county median, integer year)

Source: Census Reporter API (free, no key required, mirrors official ACS data).
If `CENSUS_API_KEY` is present in .env, the Census Bureau API is used directly
instead — generally newer and rate-limit-friendlier.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import httpx
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "processed" / "regions.duckdb"

CENSUS_REPORTER_URL = (
    "https://api.censusreporter.org/1.0/data/show/latest"
    "?table_ids=B01003,B25001,B25003,B25035&geo_ids=050|04000US12"
)
CENSUS_BUREAU_URL = "https://api.census.gov/data/2022/acs/acs5"
CENSUS_VARS = ["B01003_001E", "B25001_001E", "B25003_001E",
               "B25003_002E", "B25035_001E"]


def _older_housing_score(median_year_built: int | None) -> float:
    """Older housing -> more repair / weatherization demand per disaster.
    1980-or-earlier -> 1.0; 2010-or-later -> 0.0; linear in between.
    """
    if median_year_built is None or median_year_built <= 0:
        return 0.0
    if median_year_built <= 1980:
        return 1.0
    if median_year_built >= 2010:
        return 0.0
    return round((2010 - median_year_built) / 30.0, 3)


def _shape_record(fips: str, name: str, pop: int, units: int,
                  occ: int, owner: int, median_year: int | None) -> dict:
    return {
        "fips": fips,
        "name": name,
        "population": pop,
        "housing_units": units,
        "occupied_units": occ,
        "owner_occupied_units": owner,
        "owner_occupied_share": round(owner / occ, 4) if occ else 0.0,
        "median_year_built": median_year,
        "older_housing_score": _older_housing_score(median_year),
    }


def _fetch_via_census_reporter() -> list[dict]:
    with httpx.Client(timeout=30) as c:
        r = c.get(CENSUS_REPORTER_URL)
        r.raise_for_status()
        payload = r.json()

    geos = payload.get("geography", {})
    out: list[dict] = []
    for geo_id, tables in payload.get("data", {}).items():
        # geo_id looks like "05000US12001" — strip the summary-level prefix.
        fips = geo_id.split("US", 1)[-1]
        name = geos.get(geo_id, {}).get("name", fips)
        est = lambda table, var: tables.get(table, {}).get("estimate", {}).get(var)
        pop = int(est("B01003", "B01003001") or 0)
        units = int(est("B25001", "B25001001") or 0)
        occ = int(est("B25003", "B25003001") or 0)
        owner = int(est("B25003", "B25003002") or 0)
        my_raw = est("B25035", "B25035001")
        median_year = int(my_raw) if my_raw and my_raw > 1900 else None
        out.append(_shape_record(fips, name, pop, units, occ, owner, median_year))
    return out


def _fetch_via_census_bureau(api_key: str) -> list[dict]:
    params = {
        "get": ",".join(["NAME"] + CENSUS_VARS),
        "for": "county:*",
        "in": "state:12",
        "key": api_key,
    }
    with httpx.Client(timeout=30) as c:
        r = c.get(CENSUS_BUREAU_URL, params=params)
        r.raise_for_status()
        rows = r.json()
    headers, *data = rows
    out: list[dict] = []
    for row in data:
        rec = dict(zip(headers, row))
        fips = rec["state"] + rec["county"]
        get_int = lambda k: int(rec[k]) if rec.get(k) not in (None, "") else 0
        my_raw = rec.get("B25035_001E")
        try:
            median_year = int(my_raw) if int(my_raw) > 1900 else None
        except (TypeError, ValueError):
            median_year = None
        out.append(_shape_record(
            fips=fips,
            name=rec["NAME"],
            pop=get_int("B01003_001E"),
            units=get_int("B25001_001E"),
            occ=get_int("B25003_001E"),
            owner=get_int("B25003_002E"),
            median_year=median_year,
        ))
    return out


def fetch_fl_counties() -> list[dict]:
    key = (os.getenv("CENSUS_API_KEY") or "").strip()
    if key:
        return _fetch_via_census_bureau(key)
    return _fetch_via_census_reporter()


def ingest() -> dict:
    rows = fetch_fl_counties()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(DB_PATH.as_posix())
    con.execute("""
        CREATE TABLE IF NOT EXISTS county_demographics (
            fips TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            population INTEGER NOT NULL,
            housing_units INTEGER NOT NULL,
            occupied_units INTEGER NOT NULL,
            owner_occupied_units INTEGER NOT NULL,
            owner_occupied_share DOUBLE NOT NULL,
            median_year_built INTEGER,
            older_housing_score DOUBLE NOT NULL
        )
    """)
    con.execute("DELETE FROM county_demographics")
    con.executemany(
        """INSERT INTO county_demographics VALUES
           (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(r["fips"], r["name"], r["population"], r["housing_units"],
          r["occupied_units"], r["owner_occupied_units"],
          r["owner_occupied_share"], r["median_year_built"],
          r["older_housing_score"]) for r in rows],
    )
    n_pop = con.execute("SELECT SUM(population) FROM county_demographics").fetchone()[0]
    n_units = con.execute("SELECT SUM(housing_units) FROM county_demographics").fetchone()[0]
    con.close()
    return {
        "counties_written": len(rows),
        "fl_population": n_pop,
        "fl_housing_units": n_units,
    }


if __name__ == "__main__":
    print(ingest())
