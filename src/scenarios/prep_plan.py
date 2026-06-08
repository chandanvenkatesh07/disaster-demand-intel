from __future__ import annotations
from math import cos, radians
from shapely.geometry import LineString, Point
from . import Scenario
from ..stock_map import STOCK_PLANS


def compute_prep_plan(scenario: Scenario, stores: list[dict]) -> list[dict]:
    """Compute a per-store preparation plan for a given disaster scenario.
    
    For each store, determines the nearest storm path, interpolates time to impact,
    checks cone inclusion, and assembles a stock checklist based on alert severity.
    Returns a list of dicts for in-cone stores, sorted by time bucket and distance.
    """
    paths = scenario.paths
    alerts = scenario.synthetic_alerts
    
    # Sort alerts by severity_score descending to easily pick worst
    sorted_alerts = sorted(alerts, key=lambda a: a.severity_score, reverse=True)
    
    # Time bucket order mapping for sorting
    bucket_order = {'T-0': 0, 'T-12h': 1, 'T-24h': 2, 'T-48h': 3, 'T-72h+': 4}
    
    results = []
    
    for store in stores:
        store_point = Point(store['lon'], store['lat'])
        store_fips = store.get('fips')
        
        # Find nearest path
        min_dist = float('inf')
        nearest_path = None
        nearest_line = None
        
        for path in paths:
            line = LineString(path.waypoints)
            d = line.distance(store_point)
            if d < min_dist:
                min_dist = d
                nearest_path = path
                nearest_line = line
                
        distance_deg = min_dist
        distance_km = round(distance_deg * 111.0, 1)
        
        # Check cone inclusion
        if distance_deg > nearest_path.cone_buffer_deg:
            continue
            
        # Interpolate hours_to_impact
        hours = nearest_path.hours_to_landfall
        n_segments = len(hours) - 1
        proj_frac = nearest_line.project(store_point, normalized=True)
        segment_idx = proj_frac * n_segments
        i = int(segment_idx)
        frac = segment_idx - i
        
        # Clamp i to avoid index out of range if proj_frac is exactly 1.0
        if i >= n_segments:
            i = n_segments - 1
            frac = 0.0
            
        h1 = hours[i]
        h2 = hours[i+1]
        hours_to_impact = h1 + (h2 - h1) * frac
        hours_to_impact = round(hours_to_impact, 1)
        
        # Determine time bucket
        if hours_to_impact <= 0:
            time_bucket = 'T-0'
        elif hours_to_impact <= 12:
            time_bucket = 'T-12h'
        elif hours_to_impact <= 24:
            time_bucket = 'T-24h'
        elif hours_to_impact <= 48:
            time_bucket = 'T-48h'
        else:
            time_bucket = 'T-72h+'
            
        # Determine stock checklist
        matching_alert = None
        for alert in sorted_alerts:
            if store_fips in alert.affected_fips:
                matching_alert = alert
                break
                
        if matching_alert is None and sorted_alerts:
            matching_alert = sorted_alerts[0]
            
        if matching_alert:
            category = matching_alert.category
            if category in STOCK_PLANS:
                stock_checklist = list(STOCK_PLANS[category].items)
            else:
                stock_checklist = []
        else:
            stock_checklist = []
            
        results.append({
            'osm_id': store['osm_id'],
            'name': store['name'],
            'lat': store['lat'],
            'lon': store['lon'],
            'fips': store_fips,
            'county': store['county'],
            'path_name': nearest_path.name,
            'distance_km': distance_km,
            'hours_to_impact': hours_to_impact,
            'time_bucket': time_bucket,
            'stock_checklist': stock_checklist,
        })
        
    # Sort results by time bucket order, then distance
    results.sort(key=lambda r: (bucket_order.get(r['time_bucket'], 99), r['distance_km']))
    
    return results
