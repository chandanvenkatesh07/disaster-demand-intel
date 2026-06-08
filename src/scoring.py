"""Deterministic Demand Priority Index (DPI) for Florida counties.

Per spec:
    DPI = 0.40 * forecast_impact
        + 0.25 * pop_size
        + 0.15 * stock_urgency
        + 0.10 * housing_exposure
        + 0.10 * store_coverage_gap

All sub-scores normalize to 0..1. Reads from the DuckDB tables populated by
the ingest modules; returns a list of dicts (one per county) ranked by DPI.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb

from .stock_map import STOCK_PLANS, merge_plans

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "processed" / "regions.duckdb"

WEIGHTS = {
    "forecast_impact": 0.40,
    "pop_size": 0.25,
    "stock_urgency": 0.15,
    "housing_exposure": 0.10,
    "store_coverage_gap": 0.10,
}


@dataclass
class CountyScore:
    fips: str
    name: str
    population: int
    housing_units: int
    owner_occupied_units: int
    older_housing_score: float
    store_count: int
    stores_per_100k: float
    active_categories: list[str]  # disaster categories with active alerts
    active_alerts: list[dict] = field(default_factory=list)
    hazard_scores: dict[str, float] = field(default_factory=dict)
    sub_scores: dict[str, float] = field(default_factory=dict)
    dpi: float = 0.0
    recommended_items: list[str] = field(default_factory=list)
    stock_urgency_driver: float = 0.0
    hazard_source: str = ""


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(DB_PATH.as_posix())
    con.execute("INSTALL spatial; LOAD spatial;")
    return con


def _build_store_county(con: duckdb.DuckDBPyConnection) -> None:
    """Rebuild the store->county join. Cheap; idempotent."""
    con.execute("""
        CREATE OR REPLACE TABLE store_county AS
        SELECT s.osm_id, s.lat, s.lon, s.name AS store_name,
               c.fips, c.name AS county_name
        FROM home_depot_store s
        LEFT JOIN county_geom c
          ON ST_Within(ST_Point(s.lon, s.lat), ST_GeomFromText(c.geom_wkt))
    """)


def _log_normalize(values: list[float]) -> dict[int, float]:
    """Return a dict {index -> 0..1 score} via log1p min-max."""
    if not values:
        return {}
    logs = [math.log1p(max(0.0, v)) for v in values]
    lo, hi = min(logs), max(logs)
    span = hi - lo
    if span == 0:
        return {i: 0.0 for i in range(len(values))}
    return {i: (logs[i] - lo) / span for i in range(len(values))}


def _min_max(values: list[float], invert: bool = False) -> dict[int, float]:
    if not values:
        return {}
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        return {i: 0.0 for i in range(len(values))}
    out = {i: (v - lo) / span for i, v in enumerate(values)}
    if invert:
        out = {i: 1.0 - s for i, s in out.items()}
    return out


def compute(con: duckdb.DuckDBPyConnection | None = None) -> list[CountyScore]:
    close_con = False
    if con is None:
        con = _connect()
        close_con = True

    _build_store_county(con)

    demo = con.execute("""
        SELECT fips, name, population, housing_units, owner_occupied_units,
               older_housing_score
        FROM county_demographics ORDER BY fips
    """).fetchall()

    hazard_rows = con.execute("""
        SELECT fips, hazard, risk_score, source FROM county_hazard
    """).fetchall()
    hazard_by_fips: dict[str, dict[str, float]] = {}
    hazard_source_by_fips: dict[str, str] = {}
    for fips, hazard, score, source in hazard_rows:
        hazard_by_fips.setdefault(fips, {})[hazard] = float(score)
        hazard_source_by_fips[fips] = source

    store_rows = con.execute("""
        SELECT fips, COUNT(*) AS n FROM store_county
        WHERE fips IS NOT NULL GROUP BY fips
    """).fetchall()
    stores_by_fips = {fips: n for fips, n in store_rows}

    alert_rows = con.execute("""
        SELECT ac.fips, a.alert_id, a.event, a.category, a.severity,
               a.severity_score, a.headline, a.expires
        FROM noaa_alert_county ac
        JOIN noaa_alert a USING (alert_id)
    """).fetchall()
    alerts_by_fips: dict[str, list[dict]] = {}
    cat_severity_by_fips: dict[str, dict[str, float]] = {}
    for fips, aid, event, cat, sev, sev_score, headline, expires in alert_rows:
        alerts_by_fips.setdefault(fips, []).append({
            "alert_id": aid, "event": event, "category": cat,
            "severity": sev, "severity_score": float(sev_score),
            "headline": headline, "expires": expires,
        })
        bucket = cat_severity_by_fips.setdefault(fips, {})
        bucket[cat] = max(bucket.get(cat, 0.0), float(sev_score))

    n = len(demo)
    pop_vals = [d[2] for d in demo]
    pop_score = _log_normalize(pop_vals)

    # store coverage gap: invert stores-per-100k so low coverage -> high score
    coverage_vals = []
    for d in demo:
        fips, _name, pop, *_ = d
        store_n = stores_by_fips.get(fips, 0)
        per_100k = (store_n / pop * 100_000) if pop else 0.0
        coverage_vals.append(per_100k)
    coverage_gap = _min_max(coverage_vals, invert=True)

    scores: list[CountyScore] = []
    for i, (fips, name, pop, units, owner, older) in enumerate(demo):
        hazards = hazard_by_fips.get(fips, {})
        active = alerts_by_fips.get(fips, [])
        active_cats = sorted({a["category"] for a in active})

        # forecast_impact: severity-weighted by the worst active alert,
        # boosted by the baseline hazard score for that category.
        if active:
            forecast_impact = max(
                a["severity_score"] * (0.5 + 0.5 * hazards.get(a["category"], 0.0))
                for a in active
            )
        else:
            forecast_impact = 0.0

        # stock_urgency: from the disaster-to-stock map for active categories.
        # If no active alerts, use the single highest baseline hazard category
        # to seed reasonable stocking guidance (smaller weight scaling).
        if active_cats:
            plan = merge_plans(active_cats)
            stock_urgency = plan["urgency"]
            items = plan["items"]
        elif hazards:
            top_haz = max(hazards.items(), key=lambda kv: kv[1])
            if top_haz[0] in STOCK_PLANS and top_haz[1] > 0:
                plan = merge_plans([top_haz[0]])
                # baseline-only: discount urgency since there's no active event
                stock_urgency = plan["urgency"] * top_haz[1] * 0.6
                items = plan["items"]
            else:
                stock_urgency, items = 0.0, []
        else:
            stock_urgency, items = 0.0, []

        # housing_exposure: peak hazard score * older-housing factor.
        peak_haz = max(hazards.values()) if hazards else 0.0
        housing_exposure = peak_haz * (0.5 + 0.5 * older)

        sub = {
            "forecast_impact": round(forecast_impact, 4),
            "pop_size": round(pop_score.get(i, 0.0), 4),
            "stock_urgency": round(stock_urgency, 4),
            "housing_exposure": round(housing_exposure, 4),
            "store_coverage_gap": round(coverage_gap.get(i, 0.0), 4),
        }
        dpi = sum(WEIGHTS[k] * v for k, v in sub.items())

        scores.append(CountyScore(
            fips=fips,
            name=name,
            population=pop,
            housing_units=units,
            owner_occupied_units=owner,
            older_housing_score=older,
            store_count=stores_by_fips.get(fips, 0),
            stores_per_100k=round(coverage_vals[i], 3),
            active_categories=active_cats,
            active_alerts=active,
            hazard_scores=hazards,
            sub_scores=sub,
            dpi=round(dpi, 4),
            recommended_items=items,
            stock_urgency_driver=stock_urgency,
            hazard_source=hazard_source_by_fips.get(fips, "unknown"),
        ))

    scores.sort(key=lambda s: s.dpi, reverse=True)
    if close_con:
        con.close()
    return scores


def to_dicts(scores: list[CountyScore]) -> list[dict]:
    return [asdict(s) for s in scores]


if __name__ == "__main__":
    out = compute()
    print(f"counties scored: {len(out)}")
    print(f"top 10 by DPI:")
    for s in out[:10]:
        print(f"  {s.fips} {s.name:<28} dpi={s.dpi:.3f}  "
              f"pop={s.population:>9,d}  stores={s.store_count:>2}  "
              f"alerts={len(s.active_alerts)}  cats={s.active_categories}")
