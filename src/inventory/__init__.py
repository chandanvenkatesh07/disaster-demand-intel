from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians, sqrt, atan2, sin

"""
Hurricane preparation inventory module.
Defines SKU and DistributionCenter dataclasses,
and provides catalog and nearest-DC lookup helpers.
"""

# All stock counts are synthetic demo data, not real ERP figures.

@dataclass(frozen=True)
class SKU:
    id: str
    name: str
    item: str
    unit: str
    default_dc_stock: int

@dataclass(frozen=True)
class DistributionCenter:
    id: str
    name: str
    lat: float
    lon: float

HURRICANE_SKUS: tuple[SKU, ...] = (
    SKU('PLY-12-4X8',       'Plywood 1/2" x 4x8 sheet',          'plywood',                'sheet', 800),
    SKU('PLY-58-4X8',       'Plywood 5/8" x 4x8 sheet',          'plywood',                'sheet', 600),
    SKU('TARP-BLU-10X12',   'Blue tarp 10x12',                    'tarps',                  'each',  1200),
    SKU('TARP-HD-12X16',    'Heavy-duty tarp 12x16',              'tarps',                  'each',  800),
    SKU('GEN-3500W',        '3500W portable generator',           'generators',             'each',  250),
    SKU('GEN-7500W-DUAL',   '7500W dual-fuel generator',          'generators',             'each',  150),
    SKU('BAT-D-8PK',        'D-cell battery 8-pack',              'batteries',              'pack',  2400),
    SKU('BAT-AA-24PK',      'AA battery 24-pack',                 'batteries',              'pack',  3000),
    SKU('FLASH-LED-CR',     'LED flashlight rechargeable',        'flashlights',            'each',  1200),
    SKU('CHAIN-16IN-GAS',   '16-inch gas chainsaw',               'chainsaws',              'each',  220),
    SKU('CHAIN-14IN-ELEC',  '14-inch electric chainsaw',          'chainsaws',              'each',  280),
    SKU('ROOFCEM-1G',       'Roof cement 1 gallon',               'roof repair supplies',   'each',  900),
    SKU('ROOFTAPE-50',      'Roof seal tape 50 ft',               'roof repair supplies',   'roll',  700),
    SKU('SAND-100PK',       'Empty sandbag 100-pack',             'sandbags',               'pack',  900),
    SKU('SUMP-13HP',        '1/3 HP submersible sump pump',       'sump pumps',             'each',  300),
    SKU('SUMP-12HP-AUTO',   '1/2 HP automatic sump pump',         'sump pumps',             'each',  240),
    SKU('VAC-6GAL',         '6-gallon wet/dry vacuum',            'wet/dry vacs',           'each',  280),
    SKU('VAC-16GAL',        '16-gallon wet/dry vacuum',           'wet/dry vacs',           'each',  180),
    SKU('BAG-CONTR-42G',    '42-gallon contractor trash bags',    'contractor bags',        'pack',  1800),
    SKU('GLOVES-WORK-L',    'Work gloves, large',                 'gloves',                 'pair',  2200),
)

DISTRIBUTION_CENTERS: tuple[DistributionCenter, ...] = (
    DistributionCenter('DC-LAKELAND',  'Lakeland Regional DC',  28.0395, -81.9498),
    DistributionCenter('DC-JAX',       'Jacksonville Regional DC', 30.3322, -81.6557),
    DistributionCenter('DC-POMPANO',   'Pompano Beach Regional DC', 26.2378, -80.1248),
)

ITEM_TO_SKUS: dict[str, tuple[str, ...]] = {
    item: tuple(s.id for s in HURRICANE_SKUS if s.item == item)
    for item in dict.fromkeys(s.item for s in HURRICANE_SKUS)
}

def nearest_dc(lat: float, lon: float) -> DistributionCenter:
    '''Return the DC nearest to (lat, lon) using haversine distance.'''
    R = 6371.0
    min_dist = float('inf')
    nearest = None
    for dc in DISTRIBUTION_CENTERS:
        dlat = radians(dc.lat - lat)
        dlon = radians(dc.lon - lon)
        a = sin(dlat / 2) ** 2 + cos(radians(lat)) * cos(radians(dc.lat)) * sin(dlon / 2) ** 2
        d = 2 * R * atan2(sqrt(a), sqrt(1 - a))
        if d < min_dist:
            min_dist = d
            nearest = dc
    return nearest
