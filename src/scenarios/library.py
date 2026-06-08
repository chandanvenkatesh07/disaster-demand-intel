"""
Module-level hurricane scenario definitions.

This module provides pre-defined `Scenario` constants for simulation and
visualization exercises, along with a lookup dictionary.
"""
from __future__ import annotations

from . import Scenario, StormPath, SyntheticAlert

CHARLEY_2004 = Scenario(
    id='charley_2004',
    name='Hurricane Charley 2004 (Cat 4, Punta Gorda landfall)',
    hurricane_name='Charley',
    description='Cat 4 hurricane makes rapid landfall in Charlotte County, then tracks NE across central Florida to exit near Daytona.',
    paths=(
        StormPath(
            name='Hurricane Charley track',
            waypoints=(( -83.5, 23.0), (-82.5, 25.0), (-82.1, 26.5), (-82.07, 26.87), (-81.3, 28.5), (-80.5, 29.5)),
            hours_to_landfall=(-48.0, -24.0, -6.0, 0.0, 6.0, 12.0),
            cone_buffer_deg=0.6,
        ),
    ),
    synthetic_alerts=(
        SyntheticAlert(
            alert_id='charley/hwarn',
            event='Hurricane Warning',
            category='hurricane',
            severity='Extreme',
            severity_score=1.0,
            headline='Hurricane Charley (Cat 4) — landfall Charlotte County within 12 hours.',
            affected_fips=('12015','12071','12081','12057','12103'),
        ),
        SyntheticAlert(
            alert_id='charley/surge',
            event='Storm Surge Warning',
            category='hurricane',
            severity='Extreme',
            severity_score=1.0,
            headline='Life-threatening surge along SW Florida coast.',
            affected_fips=('12015','12071','12081','12087','12021'),
        ),
        SyntheticAlert(
            alert_id='charley/tswarn',
            event='Tropical Storm Warning',
            category='hurricane',
            severity='Severe',
            severity_score=0.85,
            headline='Tropical storm conditions extending inland across central Florida.',
            affected_fips=('12105','12095','12117','12127','12009','12055','12097'),
        ),
    ),
)

WILMA_2005 = Scenario(
    id='wilma_2005',
    name='Hurricane Wilma 2005 (Cat 3, south Florida crossing)',
    hurricane_name='Wilma',
    description='Cat 3 hurricane crosses the Florida peninsula from Cape Romano to Palm Beach, slamming south Florida metros.',
    paths=(
        StormPath(
            name='Hurricane Wilma track',
            waypoints=(( -84.5, 24.5), (-82.5, 25.0), (-81.66, 25.85), (-80.5, 26.0), (-80.05, 26.71)),
            hours_to_landfall=(-48.0, -18.0, 0.0, 6.0, 12.0),
            cone_buffer_deg=0.6,
        ),
    ),
    synthetic_alerts=(
        SyntheticAlert(
            alert_id='wilma/hwarn',
            event='Hurricane Warning',
            category='hurricane',
            severity='Extreme',
            severity_score=1.0,
            headline='Hurricane Wilma (Cat 3) — landfall Collier County, fast crossing to east coast.',
            affected_fips=('12087','12021','12051','12086','12011','12099','12071'),
        ),
        SyntheticAlert(
            alert_id='wilma/surge',
            event='Storm Surge Warning',
            category='hurricane',
            severity='Severe',
            severity_score=0.85,
            headline='Storm surge expected on both coasts.',
            affected_fips=('12087','12021','12086','12011','12099'),
        ),
        SyntheticAlert(
            alert_id='wilma/tswarn',
            event='Tropical Storm Warning',
            category='hurricane',
            severity='Severe',
            severity_score=0.85,
            headline='Tropical storm conditions across south Florida.',
            affected_fips=('12015','12111','12085','12061'),
        ),
    ),
)

TWO_PATH_UNCERTAINTY = Scenario(
    id='two_path_uncertainty',
    name='Two-path uncertainty across Florida (forecast cone split)',
    hurricane_name='Test-Cyclone',
    description='Hypothetical Cat 2 with two plausible tracks: a Tampa Bay landfall path and a Big Bend landfall path. Used to demonstrate multi-path prep planning.',
    paths=(
        StormPath(
            name='Tampa Bay landfall (Path A)',
            waypoints=(( -83.5, 24.0), (-83.0, 26.5), (-82.7, 27.5), (-82.6, 28.0), (-81.5, 28.5), (-80.7, 29.0)),
            hours_to_landfall=(-72.0, -36.0, -12.0, 0.0, 6.0, 12.0),
            cone_buffer_deg=0.8,
        ),
        StormPath(
            name='Big Bend landfall (Path B)',
            waypoints=(( -83.5, 24.0), (-83.7, 27.5), (-84.0, 29.0), (-83.5, 30.0), (-82.5, 30.5), (-81.5, 30.5)),
            hours_to_landfall=(-72.0, -36.0, -12.0, 0.0, 6.0, 12.0),
            cone_buffer_deg=0.8,
        ),
    ),
    synthetic_alerts=(
        SyntheticAlert(
            alert_id='twopath/hwatch',
            event='Hurricane Watch',
            category='hurricane',
            severity='Severe',
            severity_score=0.85,
            headline='Hurricane conditions possible along either Tampa Bay or Big Bend within 48 hours.',
            affected_fips=('12057','12103','12081','12101','12053','12017','12075','12029','12123','12129','12037','12045'),
        ),
        SyntheticAlert(
            alert_id='twopath/surgewatch',
            event='Storm Surge Watch',
            category='hurricane',
            severity='Severe',
            severity_score=0.85,
            headline='Storm surge possible along the entire west Florida coast.',
            affected_fips=('12057','12103','12081','12101','12053','12017','12075','12029','12123','12129','12037','12045','12005'),
        ),
        SyntheticAlert(
            alert_id='twopath/tswarn',
            event='Tropical Storm Warning',
            category='hurricane',
            severity='Severe',
            severity_score=0.85,
            headline='Tropical storm conditions expected for north and central Florida.',
            affected_fips=('12095','12117','12097','12105','12001','12083','12119','12069','12031','12089'),
        ),
    ),
)

SCENARIOS: dict[str, Scenario] = {s.id: s for s in (CHARLEY_2004, WILMA_2005, TWO_PATH_UNCERTAINTY)}
