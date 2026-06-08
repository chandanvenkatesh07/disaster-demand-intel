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
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import duckdb

from .ingest.noaa_alerts import ingest as refresh_noaa_alerts
from .llm_client import LLMClient
from .scenarios.library import SCENARIOS
from .scenarios.prep_plan import compute_prep_plan
from .scenarios import Scenario
from .scoring import (
    CountyScore,
    apply_filter,
    compute,
    compute_with_synthetic_alert,
)
from .inventory.replenishment import compute_shortfalls, compute_transfer_orders
from .inventory.storage import persist_orders, list_orders, update_status, clear_all as clear_transfer_orders, summary as transfer_order_summary
from collections import Counter

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


def _exec_payload(s: CountyScore) -> dict:
    top_haz = max(s.hazard_scores.items(), key=lambda kv: kv[1], default=(None, 0))
    return {
        "fips": s.fips,
        "name": s.name,
        "dpi": s.dpi,
        "population": s.population,
        "store_count": s.store_count,
        "stores_per_100k": s.stores_per_100k,
        "active_categories": s.active_categories,
        "active_alert_count": len(s.active_alerts),
        "top_hazard": top_haz[0],
        "top_hazard_score": round(top_haz[1], 3),
        "recommended_items": s.recommended_items[:5],
    }


@app.get("/summary/top")
def exec_summary(limit: int = Query(10, ge=3, le=25)) -> dict:
    scores = compute()[:limit]
    payload = [_exec_payload(s) for s in scores]
    with LLMClient() as c:
        summary = c.executive_summary(payload)
    return {"limit": limit, "regions": payload, "summary": summary}


class SearchBody(BaseModel):
    query: str = Field(..., min_length=2, max_length=300)


@app.post("/regions/search")
def search_regions(body: SearchBody) -> dict:
    scores = compute()
    with LLMClient() as c:
        parsed = c.parse_search_query(body.query)
    filtered = apply_filter(scores, parsed)
    return {
        "query": body.query,
        "parsed_filter": parsed,
        "count": len(filtered),
        "regions": [_score_to_summary(s) for s in filtered],
    }


class WhatIfBody(BaseModel):
    event: str = Field(..., min_length=2, max_length=80)
    severity: str = Field(default="Severe")
    category: str = Field(default="hurricane")


@app.post("/regions/{fips}/whatif")
def whatif(fips: str, body: WhatIfBody) -> dict:
    try:
        before, after, delta = compute_with_synthetic_alert(
            fips=fips, event=body.event,
            severity=body.severity, category=body.category,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    def _slim(s: CountyScore) -> dict:
        return {
            "dpi": s.dpi,
            "sub_scores": s.sub_scores,
            "active_categories": s.active_categories,
            "recommended_items": s.recommended_items,
        }

    with LLMClient() as c:
        explanation = c.explain_whatif(
            region_name=before.name,
            hypothetical={"event": body.event, "severity": body.severity,
                          "category": body.category},
            before=_slim(before),
            after=_slim(after),
            delta=delta,
        )
    return {
        "fips": fips,
        "name": before.name,
        "hypothetical": {"event": body.event, "severity": body.severity,
                         "category": body.category},
        "before": _slim(before),
        "after": _slim(after),
        "delta": delta,
        "explanation": explanation,
    }


@app.get("/scenarios")
def list_scenarios() -> dict:
    scenarios_list = []
    for s in SCENARIOS.values():
        affected_fips = set()
        for alert in s.synthetic_alerts:
            affected_fips.update(alert.affected_fips)
        scenarios_list.append({
            "id": s.id,
            "name": s.name,
            "hurricane_name": s.hurricane_name,
            "description": s.description,
            "path_count": len(s.paths),
            "affected_county_count": len(affected_fips),
        })
    return {"count": len(scenarios_list), "scenarios": scenarios_list}


@app.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str) -> dict:
    scenario = SCENARIOS.get(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} not found")
    
    affected_fips = set()
    for alert in scenario.synthetic_alerts:
        affected_fips.update(alert.affected_fips)
        
    paths_out = []
    for p in scenario.paths:
        paths_out.append({
            "name": p.name,
            "waypoints": [[wp[0], wp[1]] for wp in p.waypoints],
            "hours_to_landfall": list(p.hours_to_landfall),
            "cone_buffer_deg": p.cone_buffer_deg,
        })
        
    alerts_out = []
    for a in scenario.synthetic_alerts:
        alerts_out.append({
            "alert_id": a.alert_id,
            "event": a.event,
            "category": a.category,
            "severity": a.severity,
            "severity_score": a.severity_score,
            "headline": a.headline,
            "affected_fips": list(a.affected_fips),
        })
        
    return {
        "id": scenario.id,
        "name": scenario.name,
        "hurricane_name": scenario.hurricane_name,
        "description": scenario.description,
        "paths": paths_out,
        "synthetic_alerts": alerts_out,
        "affected_fips_union": sorted(affected_fips),
        "now_hours_to_landfall": scenario.now_hours_to_landfall,
    }


@app.post("/scenarios/{scenario_id}/activate")
def activate_scenario(scenario_id: str) -> dict:
    scenario = SCENARIOS.get(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} not found")
        
    con = duckdb.connect(DB_PATH.as_posix())
    try:
        con.execute("DELETE FROM noaa_alert_county")
        con.execute("DELETE FROM noaa_alert")
        
        now_iso = datetime.now(timezone.utc).isoformat()
        alerts_injected = 0
        
        for alert in scenario.synthetic_alerts:
            effective_iso = now_iso
            expires_iso = now_iso
            con.execute(
                "INSERT INTO noaa_alert (alert_id, event, category, severity, severity_score, headline, area_desc, effective, expires, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (alert.alert_id, alert.event, alert.category, alert.severity, alert.severity_score, alert.headline, f"(scenario:{scenario_id})", effective_iso, expires_iso, now_iso)
            )
            for fips in alert.affected_fips:
                con.execute(
                    "INSERT INTO noaa_alert_county (alert_id, fips) VALUES (?, ?)",
                    (alert.alert_id, fips)
                )
            alerts_injected += 1
            
        con.commit()
        
        stores = con.execute("""
            SELECT s.osm_id, s.lat, s.lon, s.name, sc.fips, sc.county_name AS county 
            FROM home_depot_store s 
            LEFT JOIN store_county sc USING (osm_id)
        """).fetchall()
        
        stores_dicts = [
            {"osm_id": r[0], "lat": r[1], "lon": r[2], "name": r[3], "fips": r[4], "county": r[5]}
            for r in stores
        ]
        
        plan = compute_prep_plan(scenario, stores_dicts)
        
        return {
            "scenario_id": scenario_id,
            "name": scenario.name,
            "alerts_injected": alerts_injected,
            "stores_in_cone": len(plan),
            "prep_plan": plan,
        }
    finally:
        con.close()


@app.post("/scenarios/clear")
def clear_scenario_alerts() -> dict:
    con = duckdb.connect(DB_PATH.as_posix())
    try:
        con.execute("DELETE FROM noaa_alert_county")
        con.execute("DELETE FROM noaa_alert")
        con.commit()
    finally:
        con.close()
    return {"cleared": True, "note": "call POST /refresh/alerts to re-pull live NOAA data."}


@app.post("/scenarios/{scenario_id}/check-inventory")
def check_inventory(scenario_id: str) -> dict:
    scenario = SCENARIOS.get(scenario_id)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario {scenario_id} not found")
        
    con = duckdb.connect(DB_PATH.as_posix())
    try:
        stores = con.execute("""
            SELECT s.osm_id, s.lat, s.lon, s.name, sc.fips, sc.county_name AS county
            FROM home_depot_store s LEFT JOIN store_county sc USING (osm_id)
        """).fetchall()
        stores_dicts = [
            {"osm_id": r[0], "lat": r[1], "lon": r[2], "name": r[3], "fips": r[4], "county": r[5]}
            for r in stores
        ]
        
        risk_rows = con.execute("SELECT fips, risk_score FROM county_hazard WHERE hazard = 'hurricane'").fetchall()
        risk_by_fips = {r[0]: r[1] for r in risk_rows}
    finally:
        con.close()
        
    prep_plan = compute_prep_plan(scenario, stores_dicts)
    shortfalls = compute_shortfalls(prep_plan, stores_dicts, risk_by_fips)
    orders = compute_transfer_orders(shortfalls, stores_dicts)
    
    clear_transfer_orders()
    persist_orders(orders)
    
    return {
        "scenario_id": scenario_id,
        "name": scenario.name,
        "stores_in_cone": len(prep_plan),
        "shortfall_count": len(shortfalls),
        "transfer_orders_created": len(orders),
        "by_urgency": dict(Counter(o.urgency for o in orders)),
        "by_source_type": dict(Counter(o.source_type for o in orders)),
    }


@app.get("/transfer-orders")
def list_transfer_orders(
    status: str | None = Query(None, regex='^(awaiting_approval|approved|rejected|fulfilled)$'),
    limit: int = Query(500, le=1000),
) -> dict:
    orders = list_orders(status, limit)
    return {
        "count": len(orders),
        "orders": orders,
        "summary": transfer_order_summary(),
    }


@app.post("/transfer-orders/{to_id}/approve")
def approve_transfer_order(to_id: str) -> dict:
    if not update_status(to_id, 'approved'):
        raise HTTPException(status_code=404, detail=f"TO {to_id} not found")
    return {"to_id": to_id, "status": "approved"}


@app.post("/transfer-orders/{to_id}/reject")
def reject_transfer_order(to_id: str) -> dict:
    if not update_status(to_id, 'rejected'):
        raise HTTPException(status_code=404, detail=f"TO {to_id} not found")
    return {"to_id": to_id, "status": "rejected"}
