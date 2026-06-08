"""Streamlit dashboard — Home Depot Disaster Demand Intelligence Map (FL).

Talks to the FastAPI backend over HTTP so the two layers stay decoupled.
Configure the backend URL via the BACKEND_URL env var (default http://127.0.0.1:8000).
"""

from __future__ import annotations

import json
import os

import httpx
import pandas as pd
import pydeck as pdk
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

st.set_page_config(
    page_title="Home Depot Disaster Demand Map — FL",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data(ttl=60)
def fetch_regions(disaster: str | None = None) -> list[dict]:
    params = {"limit": 67}
    if disaster:
        params["disaster"] = disaster
    r = httpx.get(f"{BACKEND_URL}/regions", params=params, timeout=30)
    r.raise_for_status()
    return r.json()["regions"]


@st.cache_data(ttl=60)
def fetch_region(fips: str) -> dict:
    r = httpx.get(f"{BACKEND_URL}/regions/{fips}", timeout=30)
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300)
def fetch_stores() -> list[dict]:
    r = httpx.get(f"{BACKEND_URL}/stores", timeout=30)
    r.raise_for_status()
    return r.json()["stores"]


@st.cache_data(ttl=3600)
def fetch_counties_geojson() -> dict:
    r = httpx.get(f"{BACKEND_URL}/counties.geojson", timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_explanation(fips: str) -> dict:
    r = httpx.post(f"{BACKEND_URL}/regions/{fips}/explain", timeout=120)
    r.raise_for_status()
    return r.json()


def refresh_alerts() -> dict:
    r = httpx.post(f"{BACKEND_URL}/refresh/alerts", timeout=60)
    r.raise_for_status()
    return r.json()


def _dpi_color(dpi: float) -> list[int]:
    """0..1 DPI -> green->yellow->red RGBA."""
    dpi = max(0.0, min(1.0, dpi))
    if dpi < 0.5:
        t = dpi / 0.5
        r, g, b = int(80 + (235 - 80) * t), int(180 - (180 - 200) * t), int(80 - 80 * t)
    else:
        t = (dpi - 0.5) / 0.5
        r, g, b = 235, int(200 - 200 * t), int(80 - 80 * t)
    return [r, g, b, 160]


def _enrich_geojson(geojson: dict, regions: list[dict]) -> dict:
    score_by_fips = {r["fips"]: r for r in regions}
    for f in geojson["features"]:
        fips = f["properties"]["GEOID"]
        s = score_by_fips.get(fips, {})
        f["properties"]["dpi"] = s.get("dpi", 0.0)
        f["properties"]["dpi_pct"] = round(s.get("dpi", 0.0) * 100, 1)
        f["properties"]["county_name"] = s.get("name", f["properties"].get("NAME", ""))
        f["properties"]["population"] = s.get("population", 0)
        f["properties"]["store_count"] = s.get("store_count", 0)
        f["properties"]["fill_color"] = _dpi_color(s.get("dpi", 0.0))
    return geojson


def _store_layer(stores: list[dict]) -> pdk.Layer:
    return pdk.Layer(
        "ScatterplotLayer",
        data=stores,
        get_position=["lon", "lat"],
        get_fill_color=[247, 109, 18, 220],  # Home Depot orange
        get_line_color=[40, 40, 40, 220],
        line_width_min_pixels=1,
        get_radius=2500,
        radius_min_pixels=3,
        radius_max_pixels=8,
        pickable=True,
    )


# ---------------- Sidebar ----------------

st.sidebar.title("Disaster Demand Map")
st.sidebar.caption("Florida vertical slice. Local LLM only — no paid APIs.")

disaster_filter = st.sidebar.selectbox(
    "Filter to active disaster category",
    options=["(none)", "hurricane", "flood", "wildfire",
             "winter_storm", "heat_wave", "tornado"],
    index=0,
)
filter_val = None if disaster_filter == "(none)" else disaster_filter

if st.sidebar.button("Refresh NOAA alerts"):
    with st.sidebar.status("Pulling api.weather.gov..."):
        result = refresh_alerts()
        st.cache_data.clear()
    st.sidebar.success(
        f"{result['alerts_written']} alerts, "
        f"{result['county_links_written']} county links"
    )

with st.sidebar.expander("Data sources", expanded=False):
    st.markdown("""
- **Hazard baseline:** FL-specific baseline shipped with this codebase
  (FEMA NRI bulk download URLs are no longer published — drop a
  `NRI_Counties_Florida.csv` in `data/raw/` to switch to real FEMA data).
- **Demographics:** Census ACS 5-year via Census Reporter (no key).
- **Stores:** OpenStreetMap via Overpass API (`brand:wikidata=Q864407`).
- **Active alerts:** api.weather.gov (NWS).
- **LLM:** local Qwen3.6-35B-A3B running on Mac Studio. Sees only the
  structured fact payload — never invents inventory, prices, or counts.
""")

# ---------------- Data ----------------

try:
    regions = fetch_regions(filter_val)
    stores = fetch_stores()
    counties_geo = _enrich_geojson(fetch_counties_geojson(), regions)
except httpx.HTTPError as e:
    st.error(f"Backend unreachable at {BACKEND_URL}: {e}")
    st.stop()

# ---------------- Header KPIs ----------------

active_alert_total = sum(r["active_alert_count"] for r in regions)
top_dpi = regions[0]["dpi"] if regions else 0.0
col_k1, col_k2, col_k3, col_k4 = st.columns(4)
col_k1.metric("Counties scored", len(regions))
col_k2.metric("Home Depot stores", len(stores))
col_k3.metric("Active NWS alerts (FL)", active_alert_total)
col_k4.metric("Top DPI", f"{top_dpi:.3f}")

# ---------------- Layout: map + detail ----------------

map_col, detail_col = st.columns([3, 2])

with map_col:
    st.subheader("Demand Priority Index")
    layers = [
        pdk.Layer(
            "GeoJsonLayer",
            data=counties_geo,
            get_fill_color="properties.fill_color",
            get_line_color=[60, 60, 60, 200],
            line_width_min_pixels=0.5,
            pickable=True,
            stroked=True,
            filled=True,
        ),
        _store_layer(stores),
    ]
    view = pdk.ViewState(latitude=28.0, longitude=-83.5, zoom=5.4, pitch=0)
    tooltip = {
        "html": (
            "<b>{county_name}</b><br/>"
            "DPI: {dpi_pct}%<br/>"
            "Population: {population}<br/>"
            "Home Depot stores: {store_count}"
        ),
        "style": {"color": "white", "backgroundColor": "rgba(0,0,0,0.8)"},
    }
    st.pydeck_chart(
        pdk.Deck(layers=layers, initial_view_state=view, tooltip=tooltip,
                 map_style="light"),
        use_container_width=True, height=560,
    )
    st.caption("Color: green (low DPI) → red (high DPI). "
               "Orange dots: Home Depot stores from OSM.")

    st.subheader("Top 10 counties by Demand Priority Index")
    df = pd.DataFrame(regions[:10])[
        ["fips", "name", "dpi", "population", "store_count",
         "active_alert_count", "active_categories", "hazard_source"]
    ].rename(columns={
        "name": "County",
        "dpi": "DPI",
        "population": "Pop",
        "store_count": "Stores",
        "active_alert_count": "Alerts",
        "active_categories": "Active categories",
        "hazard_source": "Hazard source",
    })
    df["DPI"] = df["DPI"].map(lambda x: f"{x:.3f}")
    df["Pop"] = df["Pop"].map(lambda x: f"{x:,}")
    st.dataframe(df, hide_index=True, use_container_width=True)

with detail_col:
    st.subheader("County detail")
    fips_options = [r["fips"] for r in regions]
    label_for = {r["fips"]: f"{r['name']} (DPI {r['dpi']:.3f})" for r in regions}
    selected = st.selectbox(
        "Choose a county",
        options=fips_options,
        format_func=lambda f: label_for[f],
        index=0,
    )
    detail = fetch_region(selected)

    st.metric("Demand Priority Index", f"{detail['dpi']:.3f}")
    st.caption(
        f"Hazard data source: `{detail['hazard_source']}` · "
        f"Population {detail['population']:,} · "
        f"{detail['store_count']} stores · "
        f"{len(detail['active_alerts'])} active alerts"
    )

    st.markdown("**Score breakdown** (each term, weighted)")
    weights = {
        "forecast_impact": 0.40, "pop_size": 0.25, "stock_urgency": 0.15,
        "housing_exposure": 0.10, "store_coverage_gap": 0.10,
    }
    breakdown_rows = []
    for k, w in weights.items():
        raw = detail["sub_scores"][k]
        breakdown_rows.append({
            "Term": k, "Weight": w, "Sub-score": round(raw, 3),
            "Contribution": round(w * raw, 4),
        })
    st.dataframe(pd.DataFrame(breakdown_rows), hide_index=True,
                 use_container_width=True)

    st.markdown("**Baseline hazard scores**")
    hz = {k: round(v, 2) for k, v in detail["hazard_scores"].items() if v > 0}
    if hz:
        hz_df = pd.DataFrame(
            sorted(hz.items(), key=lambda x: -x[1]),
            columns=["Hazard", "Score"],
        )
        st.dataframe(hz_df, hide_index=True, use_container_width=True)
    else:
        st.write("_(no baseline hazard data)_")

    if detail["active_alerts"]:
        st.markdown("**Active NWS alerts**")
        for a in detail["active_alerts"]:
            st.write(f"- **{a['event']}** ({a['severity']}) — {a['headline']}")
    else:
        st.markdown("**Active NWS alerts**")
        st.write("_None right now. Ranking is driven by baseline hazard "
                 "+ population + housing + store-coverage gap._")

    st.markdown("**Recommended stock**")
    if detail["recommended_items"]:
        st.write(", ".join(detail["recommended_items"]))
    else:
        st.write("_(none — no driving hazard)_")

    st.markdown("---")
    if st.button("Generate local-LLM explanation",
                 help="Calls Qwen3.6-35B-A3B on the Mac Studio. ~5-15s on first run.",
                 type="primary"):
        with st.spinner("Asking local Qwen..."):
            exp = fetch_explanation(selected)
        st.success("Done")
        for b in exp["explanation"]["bullets"]:
            st.markdown(f"- {b}")
        if exp["explanation"].get("stock_summary"):
            st.info(exp["explanation"]["stock_summary"])
        with st.expander("Exact JSON sent to the model"):
            st.code(json.dumps(exp["llm_payload"], indent=2), language="json")
