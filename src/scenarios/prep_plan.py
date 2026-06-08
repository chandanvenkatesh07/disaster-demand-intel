"""Per-store hurricane preparation plan.

For a given scenario + the live store inventory, returns one row per store
inside the cone with:
  - nearest path
  - distance to the path
  - hours_to_impact = path-hours-at-this-store minus scenario clock (so it's
    the real number of hours from "now" until the storm passes this store)
  - time bucket spanning Past, T-6h .. T-5d+
  - stock checklist for hurricane prep
"""

from __future__ import annotations

from math import hypot

from shapely.geometry import LineString, Point

from . import Scenario
from ..stock_map import STOCK_PLANS

# Sorted from most-urgent to least-urgent. "Past" sorts last; it represents
# stores the storm has already passed and where prep is no longer actionable.
BUCKET_ORDER = {
    'T-6h': 0,
    'T-12h': 1,
    'T-24h': 2,
    'T-2d': 3,
    'T-3d': 4,
    'T-4d': 5,
    'T-5d': 6,
    'T-5d+': 7,
    'Past': 99,
}


def _bucket(hours: float) -> str:
    if hours < 0:
        return 'Past'
    if hours <= 6:
        return 'T-6h'
    if hours <= 12:
        return 'T-12h'
    if hours <= 24:
        return 'T-24h'
    if hours <= 48:
        return 'T-2d'
    if hours <= 72:
        return 'T-3d'
    if hours <= 96:
        return 'T-4d'
    if hours <= 120:
        return 'T-5d'
    return 'T-5d+'


def compute_prep_plan(scenario: Scenario, stores: list[dict]) -> list[dict]:
    """For each in-cone store, return a prep-plan row.

    Input stores must have: osm_id, name, lat, lon, fips, county.
    Output rows include: ...the inputs, plus path_name, distance_km,
    hours_to_impact, time_bucket, stock_checklist.

    Stores outside every path's cone_buffer_deg are excluded entirely.
    """
    sorted_alerts = sorted(
        scenario.synthetic_alerts,
        key=lambda a: a.severity_score, reverse=True,
    )
    results: list[dict] = []

    for store in stores:
        store_point = Point(store['lon'], store['lat'])
        store_fips = store.get('fips')

        # Find the nearest path.
        min_dist = float('inf')
        nearest_path = None
        nearest_line = None
        for path in scenario.paths:
            line = LineString(path.waypoints)
            d = line.distance(store_point)
            if d < min_dist:
                min_dist = d
                nearest_path = path
                nearest_line = line

        if nearest_path is None or min_dist > nearest_path.cone_buffer_deg:
            continue

        distance_km = round(min_dist * 111.0, 1)

        # Interpolate path-hours-to-landfall at the store's projected point.
        # Use cumulative arc length so a 1000 km offshore segment doesn't
        # squash the over-land time resolution.
        hours = nearest_path.hours_to_landfall
        wpts = nearest_path.waypoints
        cum = [0.0]
        for k in range(1, len(wpts)):
            cum.append(cum[-1] + hypot(wpts[k][0] - wpts[k - 1][0],
                                       wpts[k][1] - wpts[k - 1][1]))
        total = cum[-1] or 1.0
        cum_fracs = [c / total for c in cum]
        proj_frac = nearest_line.project(store_point, normalized=True)
        # Find the segment whose [cum_fracs[i], cum_fracs[i+1]] contains proj_frac.
        seg_i = 0
        for k in range(len(cum_fracs) - 1):
            if cum_fracs[k] <= proj_frac <= cum_fracs[k + 1]:
                seg_i = k
                break
        else:
            seg_i = len(cum_fracs) - 2
        span = cum_fracs[seg_i + 1] - cum_fracs[seg_i]
        local = (proj_frac - cum_fracs[seg_i]) / span if span > 0 else 0.0
        path_hours_at_store = hours[seg_i] + (hours[seg_i + 1] - hours[seg_i]) * local

        # Subtract the scenario clock: this gives hours-from-NOW.
        hours_to_impact = round(
            path_hours_at_store - scenario.now_hours_to_landfall, 1,
        )
        time_bucket = _bucket(hours_to_impact)

        # Pick the worst-severity alert that affects this store; fall back to
        # the worst alert overall. Use its category to look up the checklist.
        matching = next(
            (a for a in sorted_alerts if store_fips in a.affected_fips),
            sorted_alerts[0] if sorted_alerts else None,
        )
        if matching and matching.category in STOCK_PLANS:
            stock_checklist = list(STOCK_PLANS[matching.category].items)
        else:
            stock_checklist = []

        results.append({
            'osm_id': store['osm_id'],
            'name': store['name'],
            'lat': store['lat'],
            'lon': store['lon'],
            'fips': store_fips,
            'county': store.get('county'),
            'path_name': nearest_path.name,
            'distance_km': distance_km,
            'hours_to_impact': hours_to_impact,
            'time_bucket': time_bucket,
            'stock_checklist': stock_checklist,
        })

    results.sort(key=lambda r: (BUCKET_ORDER.get(r['time_bucket'], 99),
                                r['distance_km']))
    return results
