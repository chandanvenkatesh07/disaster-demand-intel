"""Static lookups: which FL counties are coastal, and which sub-region they
belong to. Used by the natural-language filter.
"""

from __future__ import annotations

# Counties whose boundary touches the Gulf of Mexico or the Atlantic Ocean.
COASTAL_FIPS: set[str] = {
    # Atlantic
    "12089", "12031", "12109", "12035", "12127", "12009", "12061", "12111",
    "12085", "12099", "12011", "12086",
    # Florida Keys
    "12087",
    # Gulf (south to north on the peninsula, then west across the panhandle)
    "12021", "12071", "12015", "12115", "12081", "12103", "12057", "12101",
    "12053", "12017", "12075", "12029", "12123", "12065", "12129", "12037",
    "12045", "12005", "12131", "12091", "12113", "12033",
}

# Sub-regions of Florida. Roughly: panhandle = west of Madison;
# north = Big Bend + Jacksonville belt; central = I-4 corridor + central scrub;
# south = Treasure Coast, Sarasota, and everything south of Lake Okeechobee.
PANHANDLE_FIPS: set[str] = {
    "12005", "12013", "12033", "12037", "12039", "12045", "12059", "12063",
    "12065", "12073", "12077", "12091", "12113", "12123", "12129", "12131",
    "12133",
}

NORTH_FIPS: set[str] = {
    "12001", "12003", "12007", "12019", "12023", "12029", "12031", "12035",
    "12041", "12047", "12067", "12075", "12079", "12083", "12089", "12107",
    "12109", "12121", "12125",
}

CENTRAL_FIPS: set[str] = {
    "12009", "12017", "12027", "12049", "12053", "12055", "12057", "12069",
    "12081", "12093", "12095", "12097", "12101", "12103", "12105", "12117",
    "12119",
}

SOUTH_FIPS: set[str] = {
    "12011", "12015", "12021", "12043", "12051", "12061", "12071", "12085",
    "12086", "12087", "12099", "12111", "12115",
}

REGION_TO_FIPS = {
    "panhandle": PANHANDLE_FIPS,
    "north": NORTH_FIPS,
    "central": CENTRAL_FIPS,
    "south": SOUTH_FIPS,
}


def is_coastal(fips: str) -> bool:
    return fips in COASTAL_FIPS


def matches_region(fips: str, region: str) -> bool:
    region = (region or "").lower().strip()
    if region == "coastal":
        return fips in COASTAL_FIPS
    if region == "inland":
        return fips not in COASTAL_FIPS
    bucket = REGION_TO_FIPS.get(region)
    if bucket is None:
        return True  # unknown region: don't filter anything out
    return fips in bucket
