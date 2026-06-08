"""Streamlit dashboard — Home Depot Disaster Demand Intelligence Map (FL).

Talks to the FastAPI backend over HTTP so the two layers stay decoupled.
Configure the backend URL via the BACKEND_URL env var (default http://127.0.0.1:8000).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict

import httpx
import pandas as pd
import pydeck as pdk
import streamlit as st
from shapely.geometry import LineString, mapping

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")

st.set_page_config(
    page_title="Florida Hurricane Demand Map",
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


def fetch_exec_summary(limit: int = 10) -> dict:
    r = httpx.get(f"{BACKEND_URL}/summary/top", params={"limit": limit}, timeout=120)
    r.raise_for_status()
    return r.json()


def search_regions(query: str) -> dict:
    r = httpx.post(f"{BACKEND_URL}/regions/search",
                   json={"query": query}, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_whatif(fips: str, event: str, severity: str, category: str) -> dict:
    r = httpx.post(
        f"{BACKEND_URL}/regions/{fips}/whatif",
        json={"event": event, "severity": severity, "category": category},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300)
def fetch_scenarios() -> list[dict]:
    r = httpx.get(f"{BACKEND_URL}/scenarios", timeout=15)
    r.raise_for_status()
    return r.json()["scenarios"]


def activate_scenario(scenario_id: str) -> dict:
    r = httpx.post(f"{BACKEND_URL}/scenarios/{scenario_id}/activate", timeout=60)
    r.raise_for_status()
    return r.json()


def clear_scenario() -> dict:
    r = httpx.post(f"{BACKEND_URL}/scenarios/clear", timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_scenario(scenario_id: str) -> dict:
    r = httpx.get(f"{BACKEND_URL}/scenarios/{scenario_id}", timeout=30)
    r.raise_for_status()
    return r.json()


def run_inventory_check(scenario_id: str) -> dict:
    r = httpx.post(f'{BACKEND_URL}/scenarios/{scenario_id}/check-inventory', timeout=120)
    r.raise_for_status()
    return r.json()


def fetch_transfer_orders(status: str | None = None, limit: int = 100) -> dict:
    params = {'limit': limit}
    if status: params['status'] = status
    r = httpx.get(f'{BACKEND_URL}/transfer-orders', params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def update_transfer_order(to_id: str, action: str) -> dict:
    '''action is 'approve' or 'reject'.'''
    r = httpx.post(f'{BACKEND_URL}/transfer-orders/{to_id}/{action}', timeout=15)
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


def _scenario_layers(geom: dict) -> list[pdk.Layer]:
    '''Build pydeck layers (cone polygons + storm-track lines) for an active scenario.

    geom is the response from GET /scenarios/{id}: it has a 'paths' list, each entry
    with 'waypoints' (list of [lon,lat]), 'hours_to_landfall', 'cone_buffer_deg'.
    '''
    path_rows = []
    cone_rows = []
    for p in geom.get('paths', []):
        waypoints = [[w[0], w[1]] for w in p['waypoints']]
        path_rows.append({'name': p['name'], 'path': waypoints})
        line = LineString(p['waypoints'])
        buf = line.buffer(p.get('cone_buffer_deg', 1.0))
        # buf may be a Polygon; convert exterior to a list of [lon,lat] pairs.
        if buf.is_empty:
            continue
        exterior = list(buf.exterior.coords)
        cone_rows.append({'name': p['name'], 'polygon': [[c[0], c[1]] for c in exterior]})

    layers = []
    if cone_rows:
        layers.append(pdk.Layer(
            'PolygonLayer',
            data=cone_rows,
            get_polygon='polygon',
            get_fill_color=[255, 200, 60, 60],
            get_line_color=[210, 160, 30, 200],
            line_width_min_pixels=1,
            stroked=True,
            filled=True,
            pickable=True,
        ))
    if path_rows:
        layers.append(pdk.Layer(
            'PathLayer',
            data=path_rows,
            get_path='path',
            get_color=[200, 30, 30, 220],
            get_width=4,
            width_min_pixels=2,
            pickable=True,
        ))
    return layers


# ---------------- Sidebar ----------------

st.sidebar.title("FL Hurricane Demand")
st.sidebar.caption("Hurricane-only build. Local LLM only — no paid APIs.")

with st.sidebar.expander("Example scenarios", expanded=False):
    scenarios = fetch_scenarios()
    for s in scenarios:
        if st.button(s["name"], key=f"scenario_{s['id']}"):
            act_res = activate_scenario(s["id"])
            geom_res = fetch_scenario(s["id"])
            st.session_state["active_scenario"] = act_res
            st.session_state["active_scenario_geom"] = geom_res
            st.cache_data.clear()
            st.rerun()

if "active_scenario" in st.session_state:
    st.sidebar.caption(f"Active: {st.session_state['active_scenario']['name']}")
    if st.sidebar.button("Exit demo mode", use_container_width=True):
        clear_scenario()
        del st.session_state["active_scenario"]
        del st.session_state["active_scenario_geom"]
        st.cache_data.clear()
        st.rerun()

st.sidebar.markdown("---")

# Hurricane is the only category in this build; the API filter is kept for
# forward-compat but we just hard-wire to "show all counties with any active
# hurricane alert" or "show every county". One checkbox is enough UI.
only_active = st.sidebar.checkbox(
    "Show only counties with an active hurricane alert", value=False,
)
filter_val = "hurricane" if only_active else None
disaster_filter = "hurricane" if only_active else "(none)"

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
    # NL search result takes precedence over the disaster-category filter.
    search_state = st.session_state.get("search_result")
    if search_state:
        regions = search_state["regions"]
        # Hydrate the missing fields the dashboard expects on each row.
        for r in regions:
            r.setdefault("hazard_source", "fl_baseline_v1")
            r.setdefault("active_alert_count", 0)
            r.setdefault("active_categories", [])
    else:
        regions = fetch_regions(filter_val)
    stores = fetch_stores()
    counties_geo = _enrich_geojson(fetch_counties_geojson(), regions)
except httpx.HTTPError as e:
    st.error(f"Backend unreachable at {BACKEND_URL}: {e}")
    st.stop()

if search_state := st.session_state.get("search_result"):
    st.info(
        f"**Search:** _{search_state['query']}_  ·  "
        f"**Parsed filter:** `{search_state['parsed_filter']}`  ·  "
        f"{search_state['count']} matches"
    )

# ---------------- Header KPIs ----------------

active_alert_total = sum(r.get("active_alert_count", 0) for r in regions)
top_dpi = regions[0]["dpi"] if regions else 0.0
col_k1, col_k2, col_k3, col_k4 = st.columns(4)
col_k1.metric("Counties scored", len(regions))
col_k2.metric("Home Depot stores", len(stores))
col_k3.metric("Active hurricane alerts", active_alert_total)
col_k4.metric("Top DPI", f"{top_dpi:.3f}")

if "active_scenario" in st.session_state:
    st.warning(
        "DEMO MODE — hypothetical scenario active: {}. Real NWS alerts are cleared.".format(
            st.session_state["active_scenario"]["name"]
        )
    )

with st.expander("Executive summary (local LLM, ~5-15s)", expanded=False):
    summary_limit = st.slider("Counties to summarise", 5, 20, 10,
                              key="exec_summary_limit")
    if st.button("Generate executive summary", key="exec_summary_btn"):
        with st.spinner("Asking local Qwen for a cluster summary..."):
            try:
                exec_data = fetch_exec_summary(summary_limit)
                st.session_state["exec_summary"] = exec_data
            except httpx.HTTPError as e:
                st.error(f"Summary failed: {e}")
    if "exec_summary" in st.session_state:
        s = st.session_state["exec_summary"]["summary"]
        st.markdown(f"**Summary:** {s['paragraph']}")
        if s.get("themes"):
            st.markdown("**Themes:**")
            for t in s["themes"]:
                st.markdown(f"- {t}")

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
    if 'active_scenario_geom' in st.session_state:
        layers.extend(_scenario_layers(st.session_state['active_scenario_geom']))
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
    if not regions:
        st.info(
            f"No counties match the active filter "
            f"(disaster = `{disaster_filter}`). FL has no active NWS alerts "
            "of that type right now. Set the filter back to `(none)` to see "
            "baseline-driven ranking."
        )
    else:
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

    if "active_scenario" in st.session_state:
        st.markdown("---")
        st.subheader("Store preparation plan")
        prep_plan = st.session_state["active_scenario"]["prep_plan"]
        buckets = ["T-6h", "T-12h", "T-24h", "T-2d", "T-3d", "T-4d",
                   "T-5d", "T-5d+", "Past"]
        bucket_counts = defaultdict(int)
        for entry in prep_plan:
            bucket_counts[entry["time_bucket"]] += 1

        summary_data = []
        for b in buckets:
            summary_data.append({"Time Bucket": b, "Stores": bucket_counts.get(b, 0)})
        st.dataframe(pd.DataFrame(summary_data), hide_index=True, use_container_width=True)

        checklist_items = prep_plan[0].get("stock_checklist", []) if prep_plan else []
        if checklist_items:
            st.info(f"Checklist: {', '.join(checklist_items)}")

        for b in buckets:
            bucket_entries = [e for e in prep_plan if e["time_bucket"] == b]
            if bucket_entries:
                st.markdown(f"**{b} — {len(bucket_entries)} stores**")
                bucket_df = pd.DataFrame(bucket_entries)[
                    ["county", "name", "path_name", "distance_km", "hours_to_impact"]
                ].rename(
                    columns={
                        "county": "County",
                        "name": "Store",
                        "path_name": "Path",
                        "distance_km": "Distance km",
                        "hours_to_impact": "Hours-to-impact",
                    }
                )
                st.dataframe(bucket_df, hide_index=True, use_container_width=True)

        st.markdown('---')
        st.subheader('Inventory check — Transfer Orders')
        st.caption('DEMO DATA: inventory levels and prior-season sales are synthetic, not from a real ERP feed. Workflow logic is real.')

        if st.button('Run inventory check', key='inv_check_btn', type='primary'):
            sid = st.session_state['active_scenario']['scenario_id'] if 'scenario_id' in st.session_state['active_scenario'] else None
            # The activate response sometimes uses 'scenario_id' or just sits under 'name'. Pull it from active_scenario_geom for safety.
            if sid is None:
                sid = st.session_state.get('active_scenario_geom', {}).get('id')
            if sid:
                with st.spinner('Computing shortfalls + transfer orders...'):
                    try:
                        inv_result = run_inventory_check(sid)
                        st.session_state['inventory_check'] = inv_result
                    except httpx.HTTPError as e:
                        st.error(f'Inventory check failed: {e}')

        if 'inventory_check' in st.session_state:
            ic = st.session_state['inventory_check']
            m1, m2, m3 = st.columns(3)
            m1.metric('Stores in cone', ic.get('stores_in_cone', 0))
            m2.metric('SKU shortfalls', ic.get('shortfall_count', 0))
            m3.metric('Transfer orders created', ic.get('transfer_orders_created', 0))
            by_u = ic.get('by_urgency', {})
            if by_u:
                st.markdown('**By urgency:** ' + '  ·  '.join(f'{k}: {v}' for k, v in by_u.items()))
            by_s = ic.get('by_source_type', {})
            if by_s:
                st.markdown('**By source:** ' + '  ·  '.join(f'{k}: {v}' for k, v in by_s.items()))

            st.markdown('**Pending Transfer Orders — awaiting approval**')
            status_filter = st.selectbox('Status filter',
                options=['awaiting_approval','approved','rejected','any'],
                index=0, key='to_status_filter')
            limit = st.slider('Max rows', 10, 200, 30, key='to_limit')
            api_status = None if status_filter == 'any' else status_filter
            try:
                tos = fetch_transfer_orders(status=api_status, limit=limit)
            except httpx.HTTPError as e:
                st.error(f'fetch TOs failed: {e}')
                tos = {'orders': [], 'summary': {}}

            st.caption(f"Showing {len(tos['orders'])} of {sum(tos['summary'].values())} total. Summary: {tos['summary']}")

            if tos['orders']:
                df = pd.DataFrame(tos['orders'])[
                    ['to_id','source_type','source_name','dest_name','dest_county',
                     'sku_id','units','urgency','status','rationale']
                ].rename(columns={
                    'to_id': 'TO#', 'source_type':'Type', 'source_name':'From',
                    'dest_name':'To', 'dest_county':'County', 'sku_id':'SKU',
                    'units':'Units', 'urgency':'Urgency', 'status':'Status',
                    'rationale':'Why',
                })
                st.dataframe(df, hide_index=True, use_container_width=True)

                # Bulk + per-row actions
                b1, b2 = st.columns(2)
                if b1.button('Approve all visible', key='bulk_approve'):
                    for o in tos['orders']:
                        if o['status'] == 'awaiting_approval':
                            try:
                                update_transfer_order(o['to_id'], 'approve')
                            except httpx.HTTPError:
                                pass
                    st.success(f'Approved {len([o for o in tos["orders"] if o["status"] == "awaiting_approval"])} TOs')
                    st.rerun()
                if b2.button('Reject all visible', key='bulk_reject'):
                    for o in tos['orders']:
                        if o['status'] == 'awaiting_approval':
                            try:
                                update_transfer_order(o['to_id'], 'reject')
                            except httpx.HTTPError:
                                pass
                    st.warning(f'Rejected visible TOs')
                    st.rerun()

                # Single-row picker
                st.markdown('Single-TO action:')
                c1, c2, c3 = st.columns([3,1,1])
                pick = c1.selectbox('TO id', options=[o['to_id'] for o in tos['orders']],
                                    key='single_to_pick', label_visibility='collapsed')
                if c2.button('Approve', key='single_approve'):
                    update_transfer_order(pick, 'approve'); st.rerun()
                if c3.button('Reject', key='single_reject'):
                    update_transfer_order(pick, 'reject'); st.rerun()
            else:
                st.info('No transfer orders match this filter.')

with detail_col:
    st.subheader("County detail")
    if not regions:
        st.info("Pick a county from the unfiltered view, or clear the disaster filter.")
        st.stop()
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
                 help="Calls the local Qwen. ~5-15s on first run.",
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

    st.markdown("---")
    st.markdown("**What-if scenario**")
    wi_col1, wi_col2 = st.columns(2)
    with wi_col1:
        wi_event = st.selectbox(
            "Hypothetical hurricane event",
            options=["Hurricane Warning", "Hurricane Watch",
                     "Tropical Storm Warning", "Storm Surge Warning",
                     "Flash Flood Warning"],
            key="wi_event",
        )
    with wi_col2:
        wi_severity = st.selectbox(
            "Severity",
            options=["Extreme", "Severe", "Moderate", "Minor"],
            key="wi_severity",
        )
    if st.button("Run what-if", key="wi_btn"):
        with st.spinner("Recomputing + asking local Qwen..."):
            try:
                wi = fetch_whatif(
                    selected, wi_event, wi_severity, "hurricane",
                )
            except httpx.HTTPError as e:
                st.error(f"What-if failed: {e}")
                wi = None
        if wi:
            delta_dpi = wi["delta"]["dpi"]
            st.metric("DPI shift",
                      f"{wi['before']['dpi']:.3f} → {wi['after']['dpi']:.3f}",
                      delta=f"{delta_dpi:+.3f}")
            new_items = wi["delta"].get("new_items") or []
            if new_items:
                st.markdown(
                    f"**New stock items now in scope:** {', '.join(new_items)}"
                )
            for b in wi["explanation"]["bullets"]:
                st.markdown(f"- {b}")
            with st.expander("Sub-score deltas"):
                st.json(wi["delta"]["sub_scores"])
