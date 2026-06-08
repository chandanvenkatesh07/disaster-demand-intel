"""Per-county hazard risk ingestion for Florida.

FEMA NRI bulk downloads are no longer served from a stable URL as of 2026.
This module:
  1. Loads `data/raw/NRI_Counties_Florida.csv` if present (user-supplied real
     FEMA NRI county table, filtered to STATEFIPS=12 — column names below).
  2. Otherwise emits a hand-curated Florida-specific baseline that ships with
     the codebase. Every row is tagged with `source` so the UI can show which
     counties are using real FEMA data vs the baseline.

Expected FEMA NRI columns when a real CSV is supplied (subset we use):
    STCOFIPS, COUNTY, STATE,
    HRCN_RISKR, CFLD_RISKR, RFLD_RISKR, WFIR_RISKR, WNTW_RISKR,
    HWAV_RISKR, TRND_RISKR, HAIL_RISKR
where *_RISKR is the rating string ("Very Low" .. "Very High"). We map those
to 0-1 floats.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
DB_PATH = PROJECT_ROOT / "data" / "processed" / "regions.duckdb"
SOURCE_CSV = RAW_DIR / "NRI_Counties_Florida.csv"


RATING_TO_SCORE = {
    "Very Low": 0.10, "Relatively Low": 0.30, "Relatively Moderate": 0.50,
    "Relatively High": 0.70, "Very High": 0.90, "No Rating": 0.0,
    "Insufficient Data": 0.0, "Not Applicable": 0.0,
}

FEMA_TO_HAZARD = {
    "HRCN": "hurricane",
    "CFLD": "flood",          # coastal flood
    "RFLD": "river_flood",
    "WFIR": "wildfire",
    "WNTW": "winter_storm",
    "HWAV": "heat_wave",
    "TRND": "tornado",
    "HAIL": "hail",
}


# Florida-specific baseline, used when no FEMA CSV is on disk. Values reflect
# public knowledge: coastal counties (esp. Atlantic + Gulf) have very high
# hurricane / coastal-flood exposure; the Everglades fringe (Monroe, Collier,
# Miami-Dade) has the country's highest hurricane risk; the north-central
# scrub belt (Marion, Lake, Putnam, Polk) drives Florida's wildfire risk; the
# panhandle skews moderate-tornado; heat-wave exposure is uniformly high.
# Schema: fips -> {hazard: 0..1}.
FL_BASELINE: dict[str, dict[str, float]] = {
    # Atlantic coast (north to south)
    "12089": {"hurricane": 0.7, "flood": 0.6, "wildfire": 0.5, "tornado": 0.3, "heat_wave": 0.7},  # Nassau
    "12031": {"hurricane": 0.7, "flood": 0.6, "wildfire": 0.5, "tornado": 0.3, "heat_wave": 0.7},  # Duval
    "12109": {"hurricane": 0.7, "flood": 0.7, "wildfire": 0.4, "tornado": 0.3, "heat_wave": 0.7},  # St. Johns
    "12035": {"hurricane": 0.8, "flood": 0.7, "wildfire": 0.4, "tornado": 0.3, "heat_wave": 0.7},  # Flagler
    "12127": {"hurricane": 0.8, "flood": 0.7, "wildfire": 0.5, "tornado": 0.4, "heat_wave": 0.7},  # Volusia
    "12009": {"hurricane": 0.8, "flood": 0.7, "wildfire": 0.4, "tornado": 0.4, "heat_wave": 0.7},  # Brevard
    "12061": {"hurricane": 0.8, "flood": 0.7, "wildfire": 0.3, "tornado": 0.3, "heat_wave": 0.8},  # Indian River
    "12111": {"hurricane": 0.8, "flood": 0.7, "wildfire": 0.3, "tornado": 0.3, "heat_wave": 0.8},  # St. Lucie
    "12085": {"hurricane": 0.85, "flood": 0.7, "wildfire": 0.3, "tornado": 0.3, "heat_wave": 0.8}, # Martin
    "12099": {"hurricane": 0.9, "flood": 0.8, "wildfire": 0.2, "tornado": 0.3, "heat_wave": 0.9},  # Palm Beach
    "12011": {"hurricane": 0.9, "flood": 0.85, "wildfire": 0.2, "tornado": 0.3, "heat_wave": 0.9}, # Broward
    "12086": {"hurricane": 0.95, "flood": 0.9, "wildfire": 0.2, "tornado": 0.3, "heat_wave": 0.9}, # Miami-Dade
    "12087": {"hurricane": 0.95, "flood": 0.95, "wildfire": 0.1, "tornado": 0.2, "heat_wave": 0.9},# Monroe
    # Gulf coast (south to north)
    "12021": {"hurricane": 0.9, "flood": 0.85, "wildfire": 0.2, "tornado": 0.3, "heat_wave": 0.9}, # Collier
    "12071": {"hurricane": 0.9, "flood": 0.85, "wildfire": 0.3, "tornado": 0.4, "heat_wave": 0.9}, # Lee
    "12015": {"hurricane": 0.85, "flood": 0.8, "wildfire": 0.3, "tornado": 0.4, "heat_wave": 0.9}, # Charlotte
    "12081": {"hurricane": 0.85, "flood": 0.8, "wildfire": 0.3, "tornado": 0.4, "heat_wave": 0.9}, # Manatee
    "12115": {"hurricane": 0.85, "flood": 0.8, "wildfire": 0.3, "tornado": 0.4, "heat_wave": 0.9}, # Sarasota
    "12101": {"hurricane": 0.8, "flood": 0.75, "wildfire": 0.4, "tornado": 0.4, "heat_wave": 0.8}, # Pasco
    "12103": {"hurricane": 0.85, "flood": 0.8, "wildfire": 0.3, "tornado": 0.4, "heat_wave": 0.9}, # Pinellas
    "12057": {"hurricane": 0.85, "flood": 0.8, "wildfire": 0.3, "tornado": 0.4, "heat_wave": 0.9}, # Hillsborough
    "12053": {"hurricane": 0.75, "flood": 0.6, "wildfire": 0.4, "tornado": 0.4, "heat_wave": 0.8}, # Hernando
    "12017": {"hurricane": 0.75, "flood": 0.55, "wildfire": 0.5, "tornado": 0.4, "heat_wave": 0.8},# Citrus
    "12075": {"hurricane": 0.7, "flood": 0.6, "wildfire": 0.5, "tornado": 0.4, "heat_wave": 0.8},  # Levy
    "12029": {"hurricane": 0.65, "flood": 0.55, "wildfire": 0.5, "tornado": 0.4, "heat_wave": 0.7},# Dixie
    "12123": {"hurricane": 0.65, "flood": 0.55, "wildfire": 0.5, "tornado": 0.4, "heat_wave": 0.7},# Taylor
    "12065": {"hurricane": 0.55, "flood": 0.5, "wildfire": 0.5, "tornado": 0.4, "heat_wave": 0.7}, # Jefferson
    "12129": {"hurricane": 0.6, "flood": 0.5, "wildfire": 0.5, "tornado": 0.5, "heat_wave": 0.7},  # Wakulla
    "12037": {"hurricane": 0.65, "flood": 0.55, "wildfire": 0.5, "tornado": 0.5, "heat_wave": 0.7},# Franklin
    "12045": {"hurricane": 0.7, "flood": 0.6, "wildfire": 0.4, "tornado": 0.5, "heat_wave": 0.7},  # Gulf
    "12005": {"hurricane": 0.85, "flood": 0.65, "wildfire": 0.4, "tornado": 0.5, "heat_wave": 0.7},# Bay
    "12131": {"hurricane": 0.85, "flood": 0.65, "wildfire": 0.4, "tornado": 0.5, "heat_wave": 0.7},# Walton
    "12091": {"hurricane": 0.85, "flood": 0.6, "wildfire": 0.4, "tornado": 0.5, "heat_wave": 0.7}, # Okaloosa
    "12113": {"hurricane": 0.85, "flood": 0.6, "wildfire": 0.4, "tornado": 0.5, "heat_wave": 0.7}, # Santa Rosa
    "12033": {"hurricane": 0.9, "flood": 0.7, "wildfire": 0.4, "tornado": 0.5, "heat_wave": 0.7},  # Escambia
    # Inland north / panhandle
    "12059": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.5, "tornado": 0.55, "heat_wave": 0.7}, # Holmes
    "12133": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.5, "tornado": 0.55, "heat_wave": 0.7}, # Washington
    "12013": {"hurricane": 0.55, "flood": 0.4, "wildfire": 0.5, "tornado": 0.55, "heat_wave": 0.7},# Calhoun
    "12077": {"hurricane": 0.55, "flood": 0.45, "wildfire": 0.5, "tornado": 0.55, "heat_wave": 0.7},# Liberty
    "12063": {"hurricane": 0.55, "flood": 0.45, "wildfire": 0.5, "tornado": 0.55, "heat_wave": 0.7},# Jackson
    "12039": {"hurricane": 0.55, "flood": 0.45, "wildfire": 0.5, "tornado": 0.55, "heat_wave": 0.7},# Gadsden
    "12073": {"hurricane": 0.55, "flood": 0.45, "wildfire": 0.55, "tornado": 0.55, "heat_wave": 0.7},# Leon
    "12079": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.55, "tornado": 0.55, "heat_wave": 0.7}, # Madison
    "12121": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.55, "tornado": 0.55, "heat_wave": 0.7}, # Suwannee
    "12067": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.55, "tornado": 0.55, "heat_wave": 0.7}, # Lafayette
    "12041": {"hurricane": 0.55, "flood": 0.4, "wildfire": 0.55, "tornado": 0.5, "heat_wave": 0.7}, # Gilchrist
    "12001": {"hurricane": 0.55, "flood": 0.45, "wildfire": 0.6, "tornado": 0.5, "heat_wave": 0.7}, # Alachua
    "12107": {"hurricane": 0.55, "flood": 0.45, "wildfire": 0.6, "tornado": 0.5, "heat_wave": 0.7}, # Putnam
    "12019": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.55, "tornado": 0.5, "heat_wave": 0.7},  # Clay
    "12003": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.55, "tornado": 0.5, "heat_wave": 0.7},  # Baker
    "12023": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.55, "tornado": 0.5, "heat_wave": 0.7},  # Columbia
    "12047": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.55, "tornado": 0.5, "heat_wave": 0.7},  # Hamilton
    "12125": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.55, "tornado": 0.5, "heat_wave": 0.7},  # Union
    "12007": {"hurricane": 0.5, "flood": 0.4, "wildfire": 0.55, "tornado": 0.5, "heat_wave": 0.7},  # Bradford
    # Central FL (high wildfire belt)
    "12083": {"hurricane": 0.6, "flood": 0.45, "wildfire": 0.75, "tornado": 0.5, "heat_wave": 0.8}, # Marion
    "12069": {"hurricane": 0.65, "flood": 0.5, "wildfire": 0.75, "tornado": 0.5, "heat_wave": 0.8}, # Lake
    "12117": {"hurricane": 0.65, "flood": 0.5, "wildfire": 0.65, "tornado": 0.5, "heat_wave": 0.8}, # Seminole
    "12095": {"hurricane": 0.7, "flood": 0.55, "wildfire": 0.65, "tornado": 0.5, "heat_wave": 0.8}, # Orange
    "12097": {"hurricane": 0.7, "flood": 0.55, "wildfire": 0.65, "tornado": 0.5, "heat_wave": 0.8}, # Osceola
    "12105": {"hurricane": 0.7, "flood": 0.55, "wildfire": 0.7, "tornado": 0.5, "heat_wave": 0.8},  # Polk
    "12055": {"hurricane": 0.7, "flood": 0.5, "wildfire": 0.65, "tornado": 0.5, "heat_wave": 0.8},  # Highlands
    "12027": {"hurricane": 0.7, "flood": 0.55, "wildfire": 0.6, "tornado": 0.5, "heat_wave": 0.8},  # DeSoto
    "12049": {"hurricane": 0.7, "flood": 0.55, "wildfire": 0.6, "tornado": 0.5, "heat_wave": 0.8},  # Hardee
    "12051": {"hurricane": 0.75, "flood": 0.65, "wildfire": 0.5, "tornado": 0.5, "heat_wave": 0.85},# Hendry
    "12043": {"hurricane": 0.75, "flood": 0.6, "wildfire": 0.55, "tornado": 0.5, "heat_wave": 0.85},# Glades
    "12093": {"hurricane": 0.75, "flood": 0.65, "wildfire": 0.5, "tornado": 0.5, "heat_wave": 0.85},# Okeechobee
    "12025_DEPRECATED_PRE2008": {},  # Dade renamed to Miami-Dade (12086) — kept for clarity, not used.
    "12119": {"hurricane": 0.55, "flood": 0.4, "wildfire": 0.55, "tornado": 0.5, "heat_wave": 0.7}, # Sumter
}

FL_COUNTY_NAMES: dict[str, str] = {
    "12001":"Alachua","12003":"Baker","12005":"Bay","12007":"Bradford","12009":"Brevard",
    "12011":"Broward","12013":"Calhoun","12015":"Charlotte","12017":"Citrus","12019":"Clay",
    "12021":"Collier","12023":"Columbia","12027":"DeSoto","12029":"Dixie","12031":"Duval",
    "12033":"Escambia","12035":"Flagler","12037":"Franklin","12039":"Gadsden","12041":"Gilchrist",
    "12043":"Glades","12045":"Gulf","12047":"Hamilton","12049":"Hardee","12051":"Hendry",
    "12053":"Hernando","12055":"Highlands","12057":"Hillsborough","12059":"Holmes",
    "12061":"Indian River","12063":"Jackson","12065":"Jefferson","12067":"Lafayette",
    "12069":"Lake","12071":"Lee","12073":"Leon","12075":"Levy","12077":"Liberty",
    "12079":"Madison","12081":"Manatee","12083":"Marion","12085":"Martin","12086":"Miami-Dade",
    "12087":"Monroe","12089":"Nassau","12091":"Okaloosa","12093":"Okeechobee","12095":"Orange",
    "12097":"Osceola","12099":"Palm Beach","12101":"Pasco","12103":"Pinellas","12105":"Polk",
    "12107":"Putnam","12109":"St. Johns","12111":"St. Lucie","12113":"Santa Rosa","12115":"Sarasota",
    "12117":"Seminole","12119":"Sumter","12121":"Suwannee","12123":"Taylor","12125":"Union",
    "12127":"Volusia","12129":"Wakulla","12131":"Walton","12133":"Washington",
}

# Hurricane-only build: we keep the FL_BASELINE dict intact so a future
# multi-hazard expansion is easy, but ingest writes hurricane scores only.
HAZARDS = ["hurricane"]


@dataclass
class HazardRow:
    fips: str
    county: str
    hazard: str
    risk_score: float
    source: str  # 'fema_nri' or 'fl_baseline_v1'


def _load_from_fema_csv(path: Path) -> list[HazardRow]:
    df = pd.read_csv(path, dtype={"STCOFIPS": str})
    df = df[df["STCOFIPS"].str.startswith("12")]
    rows: list[HazardRow] = []
    for _, r in df.iterrows():
        fips = str(r["STCOFIPS"]).zfill(5)
        county = r.get("COUNTY", FL_COUNTY_NAMES.get(fips, fips))
        for fema_code, hazard in FEMA_TO_HAZARD.items():
            col = f"{fema_code}_RISKR"
            if col in df.columns:
                score = RATING_TO_SCORE.get(str(r[col]).strip(), 0.0)
                rows.append(HazardRow(fips, county, hazard, score, "fema_nri"))
    return rows


def _load_from_baseline() -> list[HazardRow]:
    rows: list[HazardRow] = []
    for fips, name in FL_COUNTY_NAMES.items():
        scores = FL_BASELINE.get(fips, {})
        for hazard in HAZARDS:
            rows.append(HazardRow(
                fips=fips,
                county=name,
                hazard=hazard,
                risk_score=float(scores.get(hazard, 0.0)),
                source="fl_baseline_v1",
            ))
    return rows


def ingest() -> dict:
    rows = (
        _load_from_fema_csv(SOURCE_CSV)
        if SOURCE_CSV.exists()
        else _load_from_baseline()
    )
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(DB_PATH.as_posix())
    con.execute("""
        CREATE TABLE IF NOT EXISTS county_hazard (
            fips TEXT NOT NULL,
            county TEXT NOT NULL,
            hazard TEXT NOT NULL,
            risk_score DOUBLE NOT NULL,
            source TEXT NOT NULL,
            PRIMARY KEY (fips, hazard)
        )
    """)
    con.execute("DELETE FROM county_hazard")
    con.executemany(
        "INSERT INTO county_hazard VALUES (?, ?, ?, ?, ?)",
        [(r.fips, r.county, r.hazard, r.risk_score, r.source) for r in rows],
    )
    sources = con.execute(
        "SELECT source, COUNT(*) FROM county_hazard GROUP BY source"
    ).fetchall()
    con.close()
    return {
        "rows_written": len(rows),
        "counties": len(FL_COUNTY_NAMES),
        "source_breakdown": dict(sources),
    }


if __name__ == "__main__":
    print(ingest())
