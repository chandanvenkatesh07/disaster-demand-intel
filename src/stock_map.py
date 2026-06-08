"""Static disaster-category → stock-category map and urgency weights.

Mirrors the table in the project spec. Pure-Python, no LLM, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StockPlan:
    category: str
    urgency: float           # 0..1, drives the 0.15 stock_urgency DPI term
    items: tuple[str, ...]


STOCK_PLANS: dict[str, StockPlan] = {
    "hurricane": StockPlan(
        category="hurricane",
        urgency=1.00,
        items=("plywood", "tarps", "generators", "batteries",
               "flashlights", "chainsaws", "roof repair supplies"),
    ),
    "flood": StockPlan(
        category="flood",
        urgency=0.90,
        items=("sump pumps", "wet/dry vacs", "fans", "dehumidifiers",
               "mold remediation supplies", "contractor bags", "gloves"),
    ),
    "river_flood": StockPlan(
        category="flood",
        urgency=0.90,
        items=("sump pumps", "wet/dry vacs", "sandbags", "fans",
               "dehumidifiers", "contractor bags"),
    ),
    "wildfire": StockPlan(
        category="wildfire",
        urgency=0.85,
        items=("respirators", "air filters", "air purifiers", "hoses",
               "fire extinguishers", "defensible-space tools"),
    ),
    "winter_storm": StockPlan(
        category="winter_storm",
        urgency=0.80,
        items=("snow shovels", "ice melt", "pipe insulation", "generators",
               "heaters", "roof rakes"),
    ),
    "heat_wave": StockPlan(
        category="heat_wave",
        urgency=0.70,
        items=("fans", "portable AC units", "water storage", "coolers",
               "shade supplies"),
    ),
    "tornado": StockPlan(
        category="tornado",
        urgency=0.85,
        items=("tarps", "plywood", "roof patching", "flashlights",
               "batteries", "chainsaws", "cleanup tools"),
    ),
    "hail": StockPlan(
        category="hail",
        urgency=0.65,
        items=("tarps", "roof patching", "window-covering plywood",
               "cleanup tools"),
    ),
}


def merge_plans(categories: list[str]) -> dict:
    """For a set of active disaster categories, return the union of stock items
    plus the max urgency across categories."""
    cats = [c for c in categories if c in STOCK_PLANS]
    if not cats:
        return {"urgency": 0.0, "items": [], "drivers": []}
    seen: set[str] = set()
    items: list[str] = []
    for c in cats:
        for it in STOCK_PLANS[c].items:
            if it not in seen:
                seen.add(it)
                items.append(it)
    urgency = max(STOCK_PLANS[c].urgency for c in cats)
    return {
        "urgency": urgency,
        "items": items,
        "drivers": cats,
    }
