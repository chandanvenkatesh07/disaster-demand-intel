"""Hurricane stock plan.

This repo is scoped to hurricanes. Storm surge, flash floods during a tropical
event, and tropical storm conditions all share the hurricane stock list.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StockPlan:
    category: str
    urgency: float           # 0..1, drives the 0.15 stock_urgency DPI term
    items: tuple[str, ...]


HURRICANE_PLAN = StockPlan(
    category="hurricane",
    urgency=1.00,
    items=(
        "plywood", "tarps", "generators", "batteries", "flashlights",
        "chainsaws", "roof repair supplies", "sandbags", "sump pumps",
        "wet/dry vacs", "contractor bags", "gloves",
    ),
)


STOCK_PLANS: dict[str, StockPlan] = {"hurricane": HURRICANE_PLAN}


def merge_plans(categories: list[str]) -> dict:
    """Hurricane is the only category in this build, but the signature is
    preserved so the scoring/api layers keep working unchanged."""
    cats = [c for c in categories if c in STOCK_PLANS]
    if not cats:
        return {"urgency": 0.0, "items": [], "drivers": []}
    return {
        "urgency": HURRICANE_PLAN.urgency,
        "items": list(HURRICANE_PLAN.items),
        "drivers": cats,
    }
