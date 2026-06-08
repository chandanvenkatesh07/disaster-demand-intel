"""
Dataclasses for defining disaster-scenario features.

This module provides frozen data structures to represent synthetic NWS alerts,
tropical cyclone tracks, and named demo scenarios for simulation and visualization.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyntheticAlert:
    alert_id: str
    event: str
    category: str
    severity: str
    severity_score: float
    headline: str
    affected_fips: tuple[str, ...]
    effective_iso: str | None = None
    expires_iso: str | None = None


@dataclass(frozen=True)
class StormPath:
    name: str
    waypoints: tuple[tuple[float, float], ...]
    hours_to_landfall: tuple[float, ...]
    cone_buffer_deg: float = 1.0


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    hurricane_name: str
    description: str
    paths: tuple[StormPath, ...]
    synthetic_alerts: tuple[SyntheticAlert, ...]
    # Where the storm is "now" relative to landfall, in hours. 0 = landfall is
    # happening now; -120 = storm is 5 days out at sea. Each store's prep
    # window is computed as (path_hours_at_store - now_hours_to_landfall),
    # so the scenario clock controls the entire prep-time view.
    now_hours_to_landfall: float = 0.0
