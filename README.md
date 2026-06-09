# Florida Hurricane Demand Intel

A small dashboard that ranks Florida counties by hurricane-driven
home-improvement demand and lays out a per-store preparation plan when a
named scenario is active. The architecture is generalizable to other
disasters and regions, but this build is **hurricane-only, Florida-only**
on purpose.

Ranking is fully deterministic. A local LLM (OpenAI-compatible endpoint) is
used only to explain a region's score in plain English — it never invents
numbers, inventory, or forecasts.

## What you get

- Choropleth of Florida counties colored by **Demand Priority Index** (DPI).
- All Home Depot store locations from OpenStreetMap, plotted as points.
- Top-10 counties table with score breakdown.
- Per-county detail panel: weighted sub-scores, hurricane risk score, active
  hurricane alerts, recommended stock categories.
- "Generate explanation" button → 3-5 grounded bullets + a one-sentence stock
  summary from the local LLM. Cached on disk.
- **Simulations** (sidebar): four pre-built hurricane situations —
  Charley 2004 (landfall now), Wilma 2005 (T-24h), two-path forecast cone
  (T-48h), and an Atlantic approach 5 days out. Each renders the storm track,
  cone of uncertainty, synthetic NWS alerts, and a per-store preparation plan
  bucketed across T-6h / T-12h / T-24h / T-2d / ... / T-5d+.

## Scoring

```
DPI = 0.40 · forecast_impact      # severity of active hurricane alerts × hurricane risk
    + 0.25 · pop_size              # log-normalized county population
    + 0.15 · stock_urgency         # hurricane stocking urgency (constant for this build)
    + 0.10 · housing_exposure      # hurricane risk × older-housing factor
    + 0.10 · store_coverage_gap    # 1 - normalized(stores per 100k pop)
```

When no hurricane alerts are active, `forecast_impact` is zero and the
ranking is driven entirely by baseline hurricane risk × pop × housing ×
coverage gap.

## Data sources

| Layer            | Source                                         | Key needed |
|------------------|------------------------------------------------|------------|
| County polygons  | Census TIGERweb GeoJSON                        | No         |
| Demographics     | Census ACS 5-year via Census Reporter API      | No         |
| Active alerts    | api.weather.gov (NWS)                          | No         |
| Stores           | OpenStreetMap via Overpass (`brand:wikidata=Q864407`) | No   |
| Hazard baseline  | FL-specific table shipped in `src/ingest/fema_nri.py` | No   |

**On FEMA NRI.** The official county-level bulk download URLs were
discontinued. The dashboard ships a hand-curated Florida hazard baseline,
clearly labeled `source = fl_baseline_v1` in the UI and the API. To use real
FEMA data, drop a `NRI_Counties_Florida.csv` (subset of the official table)
into `data/raw/` and re-run the ingest — the source column flips automatically.

## Quick start

Requires Python 3.11+ and a local OpenAI-compatible LLM endpoint.

```bash
git clone https://github.com/chandanvenkatesh07/disaster-demand-intel.git
cd disaster-demand-intel

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# edit .env: set GATEWAY_BASE_URL, GATEWAY_API_KEY, GATEWAY_MODEL

# Run all ingest modules (one-time, plus whenever you want fresher data)
python -m src.ingest.county_geom
python -m src.ingest.fema_nri
python -m src.ingest.census_acs
python -m src.ingest.osm_stores
python -m src.ingest.noaa_alerts

# Start backend (:8000) and dashboard (:8501) together
./scripts/run_dev.sh
```

Open http://127.0.0.1:8501.

To refresh alerts without restarting, click "Refresh NOAA alerts" in the
sidebar (or `POST /refresh/alerts`).

## Local LLM

The dashboard expects an OpenAI-compatible endpoint. Anything that serves
`/v1/chat/completions` works: vLLM, MLX-LM server, Ollama with the OpenAI
shim, llama.cpp server, or your own gateway.

`.env` controls it:

```
GATEWAY_BASE_URL=https://your-gateway.example.com/v1
GATEWAY_API_KEY=...
GATEWAY_MODEL=Qwen3.6-35B-A3B-oQ6-mtp
GATEWAY_USER=you
```

The client passes `X-User` on every request (useful if your gateway tracks
per-user quotas) and sends `chat_template_kwargs.enable_thinking=False` for
Qwen3-class models so chain-of-thought doesn't leak into the response.

### What the model can and cannot do

It receives a single JSON payload per region with: name, FIPS, active NWS
alerts, population, owner-occupied units, older-housing score, store count,
hazard risk scores, DPI sub-scores, and the candidate stock list from the
disaster-type map.

Constraints in the system prompt:

- Use only the facts in the payload.
- No inventing inventory, prices, sales figures, or stats beyond what's given.
- No safety or emergency instructions to the public — this is an internal
  stocking document.
- Stock recommendations must come from the supplied candidate list.
- If `forecast_events` is empty, say so explicitly and base reasoning on the
  baseline hazard scores.

Responses are cached at `runtime/explanations/<key>.json` keyed by
`(fips, sorted_alert_ids, scoring_version)` so map re-renders are free.

## Project layout

```
src/
  ingest/
    census_acs.py     Census ACS 5-year for FL counties
    county_geom.py    FL county polygons from TIGERweb
    fema_nri.py       FL hazard baseline (or real FEMA NRI if CSV present)
    noaa_alerts.py    Live NWS active alerts
    osm_stores.py     Home Depot stores from OSM (Overpass)
  llm_client.py       OpenAI-compatible client + disk cache
  scoring.py          Deterministic DPI computation
  stock_map.py        Disaster → stock categories table
  api.py              FastAPI backend (regions, stores, explain, refresh)
  dashboard.py        Streamlit UI
scripts/
  run_dev.sh          Runs backend + dashboard locally
data/
  raw/                Drop-in zone for user-supplied CSV/GeoJSON
  processed/          DuckDB lives here (regions.duckdb)
runtime/
  explanations/       Cached LLM responses (one JSON per region/state combo)
```

## API

```
GET  /regions[?limit=N&disaster=<type>]   ranked list
GET  /regions/{fips}                      full breakdown + LLM payload
POST /regions/{fips}/explain              LLM bullets (cached)
GET  /stores                              Home Depot store points
GET  /counties.geojson                    FL county polygons
POST /refresh/alerts                      re-pull NWS active alerts
GET  /healthz
```

## Known limitations

- Hurricane only. Other disaster types (wildfire, winter storms, etc.) were
  stripped in this build; the dataclasses still accept them so re-adding is
  a matter of populating `STOCK_PLANS` and `EVENT_TO_CATEGORY` again.
- FL only. Expanding to CONUS means relaxing the `state=12` filter in each
  ingest module and replacing the hazard baseline with real FEMA NRI.
- Hazard baseline is a stand-in for the official FEMA NRI scores. See above
  for how to swap in real data.
- Store count assumes OSM coverage is complete. It's good, not exhaustive —
  treat counts as a floor.
- No authentication on the local backend. Bind to 127.0.0.1 and reach it via
  SSH tunnel or Tailscale if you need remote access; do not expose to the
  public internet.
