"""Hurricane scenarios used by the Simulations feature.

All scenarios are hurricane-only. Each one has:
  - At least one storm path with waypoints + hours-to-landfall at each.
  - Synthetic NWS-style alerts.
  - `now_hours_to_landfall` — where the storm sits on its clock right now.
    0 means landfall is happening now; -120 means the storm is 5 days out.

Paths are based on real hurricane tracks where applicable (Charley 2004,
Wilma 2005); the multi-path and 5-days-out scenarios are stylised.
"""

from __future__ import annotations

from . import Scenario, StormPath, SyntheticAlert


CHARLEY_2004 = Scenario(
    id='charley_2004',
    name='Hurricane Charley 2004 — Cat 4, landfall happening now',
    hurricane_name='Charley',
    description='Cat 4 hurricane making rapid landfall in Charlotte County. Storm clock at T-0; SW-Florida stores need supplies pre-positioned immediately.',
    paths=(
        StormPath(
            name='Hurricane Charley track',
            waypoints=((-78.0, 17.0), (-81.0, 21.0), (-83.5, 23.0),
                       (-82.5, 25.0), (-82.1, 26.5), (-82.07, 26.87),
                       (-81.3, 28.5), (-80.5, 29.5)),
            hours_to_landfall=(-120.0, -72.0, -48.0, -24.0, -6.0,
                               0.0, 6.0, 12.0),
            cone_buffer_deg=0.6,
        ),
    ),
    synthetic_alerts=(
        SyntheticAlert(
            alert_id='charley/hwarn', event='Hurricane Warning',
            category='hurricane', severity='Extreme', severity_score=1.0,
            headline='Hurricane Charley (Cat 4) — landfall Charlotte County within hours.',
            affected_fips=('12015', '12071', '12081', '12057', '12103'),
        ),
        SyntheticAlert(
            alert_id='charley/surge', event='Storm Surge Warning',
            category='hurricane', severity='Extreme', severity_score=1.0,
            headline='Life-threatening surge along SW Florida coast.',
            affected_fips=('12015', '12071', '12081', '12087', '12021'),
        ),
        SyntheticAlert(
            alert_id='charley/tswarn', event='Tropical Storm Warning',
            category='hurricane', severity='Severe', severity_score=0.85,
            headline='Tropical storm conditions extending inland across central Florida.',
            affected_fips=('12105', '12095', '12117', '12127', '12009',
                           '12055', '12097'),
        ),
    ),
    now_hours_to_landfall=0.0,  # landfall happening NOW
)


WILMA_2005 = Scenario(
    id='wilma_2005',
    name='Hurricane Wilma 2005 — Cat 3, 24h before south-Florida landfall',
    hurricane_name='Wilma',
    description='Cat 3 hurricane approaching south Florida from the southwest. Storm clock at T-24h; full day to stock both coasts before peninsula crossing.',
    paths=(
        StormPath(
            name='Hurricane Wilma track',
            waypoints=((-86.0, 22.0), (-85.0, 23.5), (-84.5, 24.5),
                       (-82.5, 25.0), (-81.66, 25.85), (-80.5, 26.0),
                       (-80.05, 26.71)),
            hours_to_landfall=(-96.0, -72.0, -48.0, -18.0, 0.0, 6.0, 12.0),
            cone_buffer_deg=0.7,
        ),
    ),
    synthetic_alerts=(
        SyntheticAlert(
            alert_id='wilma/hwarn', event='Hurricane Warning',
            category='hurricane', severity='Extreme', severity_score=1.0,
            headline='Hurricane Wilma (Cat 3) — landfall Collier County, fast crossing to east coast.',
            affected_fips=('12087', '12021', '12051', '12086', '12011',
                           '12099', '12071'),
        ),
        SyntheticAlert(
            alert_id='wilma/surge', event='Storm Surge Warning',
            category='hurricane', severity='Severe', severity_score=0.85,
            headline='Storm surge expected on both coasts.',
            affected_fips=('12087', '12021', '12086', '12011', '12099'),
        ),
        SyntheticAlert(
            alert_id='wilma/tswarn', event='Tropical Storm Warning',
            category='hurricane', severity='Severe', severity_score=0.85,
            headline='Tropical storm conditions across south Florida.',
            affected_fips=('12015', '12111', '12085', '12061'),
        ),
    ),
    now_hours_to_landfall=-24.0,  # storm is 24 hours from landfall
)


TWO_PATH_UNCERTAINTY = Scenario(
    id='two_path_uncertainty',
    name='Two-path uncertainty — 48h forecast cone, Tampa Bay vs Big Bend',
    hurricane_name='Test-Cyclone',
    description='Hypothetical Cat 2 with two plausible tracks: a Tampa Bay landfall path and a Big Bend landfall path. Storm clock at T-48h; full forecast cone still in play.',
    paths=(
        StormPath(
            name='Tampa Bay landfall (Path A)',
            waypoints=((-84.5, 22.0), (-83.5, 24.0), (-83.0, 26.5),
                       (-82.7, 27.5), (-82.6, 28.0), (-81.5, 28.5),
                       (-80.7, 29.0)),
            hours_to_landfall=(-96.0, -72.0, -36.0, -12.0, 0.0, 6.0, 12.0),
            cone_buffer_deg=0.8,
        ),
        StormPath(
            name='Big Bend landfall (Path B)',
            waypoints=((-84.5, 22.0), (-83.5, 24.0), (-83.7, 27.5),
                       (-84.0, 29.0), (-83.5, 30.0), (-82.5, 30.5),
                       (-81.5, 30.5)),
            hours_to_landfall=(-96.0, -72.0, -36.0, -12.0, 0.0, 6.0, 12.0),
            cone_buffer_deg=0.8,
        ),
    ),
    synthetic_alerts=(
        SyntheticAlert(
            alert_id='twopath/hwatch', event='Hurricane Watch',
            category='hurricane', severity='Severe', severity_score=0.85,
            headline='Hurricane conditions possible along either Tampa Bay or Big Bend within 48 hours.',
            affected_fips=('12057', '12103', '12081', '12101', '12053',
                           '12017', '12075', '12029', '12123', '12129',
                           '12037', '12045'),
        ),
        SyntheticAlert(
            alert_id='twopath/surgewatch', event='Storm Surge Watch',
            category='hurricane', severity='Severe', severity_score=0.85,
            headline='Storm surge possible along the entire west Florida coast.',
            affected_fips=('12057', '12103', '12081', '12101', '12053',
                           '12017', '12075', '12029', '12123', '12129',
                           '12037', '12045', '12005'),
        ),
        SyntheticAlert(
            alert_id='twopath/tswarn', event='Tropical Storm Warning',
            category='hurricane', severity='Severe', severity_score=0.85,
            headline='Tropical storm conditions expected for north and central Florida.',
            affected_fips=('12095', '12117', '12097', '12105', '12001',
                           '12083', '12119', '12069', '12031', '12089'),
        ),
    ),
    now_hours_to_landfall=-48.0,  # storm is 48 hours from landfall
)


ATLANTIC_APPROACH_5_DAYS = Scenario(
    id='atlantic_5_days_out',
    name='Atlantic approach — Cat 3, 5 days from Miami landfall',
    hurricane_name='Atlantic-Cyclone',
    description='Major hurricane in the central Atlantic, projected to make landfall near Miami in 5 days. Storm clock at T-120h; ample lead time for staged stocking across the entire east coast.',
    paths=(
        StormPath(
            name='Atlantic approach to Miami',
            waypoints=((-55.0, 18.0), (-60.0, 19.5), (-65.0, 21.0),
                       (-70.0, 22.5), (-74.0, 24.0), (-77.0, 25.0),
                       (-79.0, 25.5), (-80.13, 25.79), (-81.5, 26.5),
                       (-82.5, 27.0)),
            hours_to_landfall=(-192.0, -168.0, -144.0, -120.0, -96.0,
                               -72.0, -36.0, 0.0, 12.0, 24.0),
            cone_buffer_deg=1.5,  # wider cone — uncertainty grows with lead time
        ),
    ),
    synthetic_alerts=(
        SyntheticAlert(
            alert_id='atl5d/hwatch', event='Hurricane Watch',
            category='hurricane', severity='Severe', severity_score=0.85,
            headline='Hurricane Watch issued for SE Florida — landfall possible in 4-5 days.',
            affected_fips=('12086', '12011', '12099', '12087', '12111',
                           '12085', '12061'),
        ),
        SyntheticAlert(
            alert_id='atl5d/tswatch', event='Tropical Storm Watch',
            category='hurricane', severity='Moderate', severity_score=0.6,
            headline='Tropical Storm Watch in effect for central and northern Florida east coast.',
            affected_fips=('12009', '12127', '12035', '12109', '12031',
                           '12089'),
        ),
    ),
    now_hours_to_landfall=-120.0,  # storm is 5 days from landfall
)


SCENARIOS: dict[str, Scenario] = {
    s.id: s for s in (
        ATLANTIC_APPROACH_5_DAYS,
        TWO_PATH_UNCERTAINTY,
        WILMA_2005,
        CHARLEY_2004,
    )
}
