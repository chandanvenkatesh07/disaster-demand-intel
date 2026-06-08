"""FastAPI backend.

Routes:
  GET  /healthz                          quick health check
  GET  /regions                          ranked county list (filterable)
  GET  /regions/{fips}                   one county + full breakdown
  POST /regions/{fips}/explain           local-LLM-generated bullets
  GET  /stores                           Home Depot store points
  GET  /counties.geojson                 FL county polygons for the choropleth
  POST /refresh/alerts                   re-pull live NOAA alerts

Scoring is recomputed on each request (cheap — 67 counties). If we ever scale
beyond FL, swap to a cached version that invalidates on alert refresh.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from .ingest.noaa_alerts import ingest as refresh_noaa_alerts
from .llm_client import LLMClient
from .scoring import CountyScore, compute

PROJECT_ROOT = Path(__file__).resolve().parent.parent
COUNTIES_GEOJSON = PROJECT_ROOT / "data" / "raw" / "fl_counties.geojson"
DB_PATH = PROJECT_ROOT / "data" / "processed" / "regions.duckdb"

app = FastAPI(title="Home Depot Disaster Demand Intelligence", version="0.1.0")


def _score_to_summary(s: CountyScore) -> dict:
    return {
        "fips": s.fips,
        "name": s.name,
        "dpi": s.dpi,
        "population": s.population,
        "store_count": s.store_count,
        "active_categories": s.active_categories,
        "active_alert_count": len(s.active_alerts),
        "hazard_source": s.hazard_source,
    }


def _score_to_full(s: CountyScore) -> dict:
    d = asdict(s)
    d["llm_payload"] = _llm_payload(s)
    return d


def _llm_payload(s: CountyScore) -> dict:
    """Build the structured-fact payload the LLM is allowed to see."""
    return {
        "region": s.name,
        "fips": s.fips,
        "forecast_events": [
            {"event": a["event"], "severity": a["severity"],
             "category": a["category"], "expires": a["expires"]}
            for a in s.active_alerts
        ],
        "population": s.population,
        "housing_exposure": {
            "older_housing_score": s.older_housing_score,
            "owner_occupied_units": s.owner_occupied_units,
        },
        "nearby_home_depot_stores": s.store_count,
        "risk_scores": {k: round(v, 3) for k, v in s.hazard_scores.items() if v > 0},
        "demand_priority_index": s.dpi,
        "score_breakdown": s.sub_scores,
        "recommended_stock_categories": s.recommended_items,
        "hazard_data_source": s.hazard_source,
    }


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "db_exists": DB_PATH.exists()}


@app.get("/regions")
def list_regions(
    limit: int = Query(67, ge=1, le=67),
    disaster: str | None = Query(None,
        description="filter to counties with this active disaster category"),
) -> dict:
    scores = compute()
    if disaster:
        scores = [s for s in scores if disaster in s.active_categories]
    return {
        "count": len(scores),
        "regions": [_score_to_summary(s) for s in scores[:limit]],
    }


@app.get("/regions/{fips}")
def get_region(fips: str) -> dict:
    scores = compute()
    for s in scores:
        if s.fips == fips:
            return _score_to_full(s)
    raise HTTPException(status_code=404, detail=f"FIPS {fips} not found")


@app.post("/regions/{fips}/explain")
def explain_region(fips: str) -> dict:
    scores = compute()
    target = next((s for s in scores if s.fips == fips), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"FIPS {fips} not found")
    payload = _llm_payload(target)
    with LLMClient() as c:
        explanation = c.explain_region(payload)
    return {
        "fips": fips,
        "name": target.name,
        "llm_payload": payload,
        "explanation": explanation,
    }


@app.get("/stores")
def list_stores() -> dict:
    import duckdb
    con = duckdb.connect(DB_PATH.as_posix())
    rows = con.execute("""
        SELECT s.osm_id, s.lat, s.lon, s.name, sc.fips, sc.county_name
        FROM home_depot_store s
        LEFT JOIN store_county sc USING (osm_id)
    """).fetchall()
    con.close()
    return {
        "count": len(rows),
        "stores": [
            {"osm_id": r[0], "lat": r[1], "lon": r[2], "name": r[3],
             "fips": r[4], "county": r[5]}
            for r in rows
        ],
    }


@app.get("/counties.geojson")
def counties_geojson() -> FileResponse:
    if not COUNTIES_GEOJSON.exists():
        raise HTTPException(
            status_code=404,
            detail="run `python -m src.ingest.county_geom` first",
        )
    return FileResponse(COUNTIES_GEOJSON, media_type="application/geo+json")


@app.post("/refresh/alerts")
def refresh_alerts() -> dict:
    return refresh_noaa_alerts()
