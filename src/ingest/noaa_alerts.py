"""Pull active NOAA / National Weather Service alerts for Florida.

NWS API:
  - Free, no key. Requires a User-Agent identifying the app per their TOS.
  - GeoJSON response with one Feature per active alert.

Each alert has:
  properties.event       e.g. "Hurricane Warning", "Heat Advisory"
  properties.severity    Extreme | Severe | Moderate | Minor | Unknown
  properties.areaDesc    "Miami-Dade, FL; Monroe, FL"
  properties.geocode.SAME  list of SAME 6-digit codes; "0" + 5-digit county FIPS
  properties.expires     ISO 8601

We map each alert to (county_fips, disaster_category, severity_score) rows so
the scoring step can join directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "processed" / "regions.duckdb"

NWS_URL = "https://api.weather.gov/alerts/active"
USER_AGENT = (
    "home-depot-disaster-map/0.1 "
    "(internal-demo; contact: chandan@example.com)"
)

SEVERITY_SCORE = {
    "Extreme": 1.0,
    "Severe": 0.85,
    "Moderate": 0.6,
    "Minor": 0.3,
    "Unknown": 0.4,
}

# NWS event types -> our disaster categories. Anything not listed is dropped
# (e.g. air-quality, marine-only events that don't drive retail demand).
EVENT_TO_CATEGORY = {
    # Tropical
    "Hurricane Warning": "hurricane",
    "Hurricane Watch": "hurricane",
    "Hurricane Force Wind Warning": "hurricane",
    "Tropical Storm Warning": "hurricane",
    "Tropical Storm Watch": "hurricane",
    "Storm Surge Warning": "hurricane",
    "Storm Surge Watch": "hurricane",
    "Tropical Depression Statement": "hurricane",
    "Extreme Wind Warning": "hurricane",
    # Flood
    "Flood Warning": "flood",
    "Flood Watch": "flood",
    "Flash Flood Warning": "flood",
    "Flash Flood Watch": "flood",
    "Coastal Flood Warning": "flood",
    "Coastal Flood Watch": "flood",
    "Coastal Flood Advisory": "flood",
    "River Flood Warning": "flood",
    "River Flood Watch": "flood",
    # Fire / wildfire
    "Red Flag Warning": "wildfire",
    "Fire Weather Watch": "wildfire",
    "Fire Warning": "wildfire",
    "Air Quality Alert": "wildfire",
    # Winter
    "Winter Storm Warning": "winter_storm",
    "Winter Storm Watch": "winter_storm",
    "Blizzard Warning": "winter_storm",
    "Ice Storm Warning": "winter_storm",
    "Hard Freeze Warning": "winter_storm",
    "Freeze Warning": "winter_storm",
    "Winter Weather Advisory": "winter_storm",
    # Heat
    "Extreme Heat Warning": "heat_wave",
    "Excessive Heat Warning": "heat_wave",
    "Extreme Heat Watch": "heat_wave",
    "Excessive Heat Watch": "heat_wave",
    "Heat Advisory": "heat_wave",
    # Severe storms / tornado / hail
    "Tornado Warning": "tornado",
    "Tornado Watch": "tornado",
    "Severe Thunderstorm Warning": "tornado",
    "Severe Thunderstorm Watch": "tornado",
}


def fetch_alerts(area: str = "FL") -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/geo+json"}
    with httpx.Client(timeout=30, headers=headers) as c:
        r = c.get(NWS_URL, params={"area": area})
        r.raise_for_status()
        return r.json()


def _fips_from_same(same_code: str) -> str | None:
    """SAME codes are 6 digits: leading '0' + 5-digit county FIPS."""
    if not same_code or not same_code.isdigit() or len(same_code) != 6:
        return None
    fips = same_code[1:]
    if not fips.startswith("12"):  # not Florida
        return None
    return fips


def parse_alerts(payload: dict) -> tuple[list[dict], list[dict]]:
    """Returns (alert_rows, alert_county_rows). One alert can hit many counties."""
    alerts: list[dict] = []
    join_rows: list[dict] = []
    for feat in payload.get("features", []):
        p = feat.get("properties", {})
        event = p.get("event")
        category = EVENT_TO_CATEGORY.get(event)
        if not category:
            continue
        severity = p.get("severity") or "Unknown"
        alert_id = p.get("id") or feat.get("id") or ""
        if not alert_id:
            continue
        same_codes = (p.get("geocode") or {}).get("SAME", []) or []
        county_fips = [f for c in same_codes if (f := _fips_from_same(c))]
        if not county_fips:
            continue
        alerts.append({
            "alert_id": alert_id,
            "event": event,
            "category": category,
            "severity": severity,
            "severity_score": SEVERITY_SCORE.get(severity, 0.4),
            "headline": p.get("headline") or "",
            "area_desc": p.get("areaDesc") or "",
            "effective": p.get("effective"),
            "expires": p.get("expires"),
        })
        for fips in county_fips:
            join_rows.append({"alert_id": alert_id, "fips": fips})
    return alerts, join_rows


def ingest() -> dict:
    payload = fetch_alerts("FL")
    alerts, join_rows = parse_alerts(payload)
    now = datetime.now(timezone.utc).isoformat()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(DB_PATH.as_posix())
    con.execute("""
        CREATE TABLE IF NOT EXISTS noaa_alert (
            alert_id TEXT PRIMARY KEY,
            event TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            severity_score DOUBLE NOT NULL,
            headline TEXT,
            area_desc TEXT,
            effective TEXT,
            expires TEXT,
            ingested_at TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS noaa_alert_county (
            alert_id TEXT NOT NULL,
            fips TEXT NOT NULL,
            PRIMARY KEY (alert_id, fips)
        )
    """)
    con.execute("DELETE FROM noaa_alert_county")
    con.execute("DELETE FROM noaa_alert")

    if alerts:
        con.executemany(
            """INSERT INTO noaa_alert VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(a["alert_id"], a["event"], a["category"], a["severity"],
              a["severity_score"], a["headline"], a["area_desc"],
              a["effective"], a["expires"], now) for a in alerts],
        )
    if join_rows:
        con.executemany(
            "INSERT INTO noaa_alert_county VALUES (?, ?)",
            [(j["alert_id"], j["fips"]) for j in join_rows],
        )

    summary = {
        "alerts_written": len(alerts),
        "county_links_written": len(join_rows),
        "by_category": dict(con.execute(
            "SELECT category, COUNT(*) FROM noaa_alert GROUP BY category"
        ).fetchall()),
        "fetched_at": now,
    }
    con.close()
    return summary


if __name__ == "__main__":
    print(ingest())
