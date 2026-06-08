"""Local-gateway LLM client.

Single entry point for the dashboard to talk to the Mac Studio's Fleet Local AI
Gateway. The gateway exposes an OpenAI-compatible API and runs Qwen3.6-35B-A3B
locally — no paid API is involved.

The client:
  - reads base URL, model, API key, and X-User from .env (never hardcoded)
  - sends the gateway's required X-User header on every request
  - puts the model into non-thinking mode and strips any <think> blocks
  - caches per-region explanations on disk by a content hash
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

load_dotenv()
logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "runtime" / "explanations"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Bump when prompt/schema changes so old cached answers are invalidated.
SCORING_VERSION = "v1"

_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class GatewayConfig:
    base_url: str
    api_key: str
    model: str
    user: str
    timeout_s: float = 60.0

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        try:
            return cls(
                base_url=os.environ["GATEWAY_BASE_URL"].rstrip("/"),
                api_key=os.environ["GATEWAY_API_KEY"],
                model=os.environ["GATEWAY_MODEL"],
                user=os.environ["GATEWAY_USER"],
            )
        except KeyError as e:
            raise RuntimeError(
                f"Missing required env var {e.args[0]}. Copy .env.example to .env "
                "and fill it in."
            ) from e


SYSTEM_PROMPT = """You are a retail demand analyst at a national home-improvement chain.

You will be given a JSON payload describing one U.S. region. Your job is to
explain — in plain English, for a regional ops manager — why this region ranks
high in the disaster-driven demand model.

Rules you must follow:
  1. Use ONLY the facts in the JSON payload. Do not invent numbers, store
     inventory, prices, sales figures, or population/housing stats beyond what
     is provided.
  2. Do not give safety or emergency instructions to the public. This is an
     internal stocking-decision document, not consumer-facing copy.
  3. If `forecast_events` is empty, say so explicitly and base your reasoning
     on the baseline FEMA risk scores instead — do not invent an active event.
  4. Stock recommendations must come from the `recommended_stock_categories`
     list in the payload. Do not add categories that are not in that list.

Reply with ONLY a JSON object, no prose, no markdown fence, with this schema:

  bullets: array of 3 to 5 strings. Each string is one full sentence
           explaining a specific reason this region ranks high, citing
           concrete facts (event names, scores, counts) from the payload.
  stock_summary: a single sentence naming the 2-4 highest-priority stock
                 categories from the supplied recommended_stock_categories
                 list and tying them to the forecast or risk profile.

Do not include any other top-level keys. Do not include example or
placeholder text in the output."""


EXEC_SUMMARY_PROMPT = """You are summarising a ranked list of counties for a
regional operations manager. You receive a JSON array of pre-computed county
facts. Output one short paragraph (3 to 5 sentences) describing the cluster
as a whole: which sub-regions or county types dominate, what hazard profile
drives the high ranks, and what stocking themes recur across the top entries.

Rules:
  - Use only the facts in the payload (county names, populations, DPI values,
    active categories, top hazards). Do not invent numbers.
  - Cite specific counties by name.
  - Do not list every county individually — describe patterns.
  - No safety or emergency instructions.

Also produce 3 short "themes" — one-liners capturing the main stocking takeaways.

Return ONLY this JSON object, no prose, no fence:
  { "paragraph": "...", "themes": ["...", "...", "..."] }
"""


SEARCH_PARSE_PROMPT = """Translate a natural-language filter request into a
structured filter object. Output ONLY a JSON object using these optional keys
— no prose, no fence, no extra keys:

  min_population, max_population        integer
  min_store_count, max_store_count      integer
  min_older_housing_score               0..1 float
  max_older_housing_score               0..1 float
  min_dpi, max_dpi                      0..1 float
  require_hazard                        one of: hurricane, flood, wildfire,
                                        heat_wave, tornado, hail, winter_storm
  min_hazard_score                      0..1 float (paired with require_hazard)
  require_active_alert                  true | false
  active_category                       same enum as require_hazard
  region                                one of: coastal, inland, panhandle,
                                        north, central, south
  sort_by                               one of: dpi, population, store_count,
                                        hazard_score (default dpi)
  limit                                 integer (default 20, max 67)

Examples:
  "coastal counties with old housing"
    -> {"region":"coastal","min_older_housing_score":0.6}
  "top 5 hurricane-exposed metros"
    -> {"require_hazard":"hurricane","min_hazard_score":0.8,"min_population":500000,"limit":5}
  "counties with few stores"
    -> {"max_store_count":3,"sort_by":"store_count"}
  "south florida with active alerts"
    -> {"region":"south","require_active_alert":true}

Omit any key you are not confident about. Do not invent fields. If the request
is unclear, return {} so the caller can show all counties."""


WHATIF_PROMPT = """A county's demand profile has been recomputed with a
HYPOTHETICAL NWS alert injected. Compare BEFORE and AFTER for an ops manager.

Rules:
  - The alert is hypothetical. Say so explicitly in at least one bullet.
  - Use only the numbers in the payload. Do not invent.
  - Output 3 bullets covering:
      (a) which sub-scores changed and by how much,
      (b) which stock items become critical that were not before,
      (c) practical implication for staffing or supply routing.
  - No public safety instructions.

Return ONLY this JSON object, no prose, no fence:
  { "bullets": ["...", "...", "..."] }
"""


class LLMClient:
    def __init__(self, config: GatewayConfig | None = None):
        self.config = config or GatewayConfig.from_env()
        self._client = httpx.Client(
            base_url=self.config.base_url,
            timeout=self.config.timeout_s,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "X-User": self.config.user,
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _chat(
        self,
        messages: list[dict],
        max_tokens: int = 600,
        temperature: float = 0.2,
    ) -> str:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
            # Qwen3 is a thinking model by default; this is the only flag the
            # underlying chat template honors to skip CoT generation.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        resp = self._client.post("/chat/completions", json=payload)
        if resp.status_code >= 500:
            resp.raise_for_status()
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def explain_region(self, region: dict) -> dict:
        """Return {"bullets": [...], "stock_summary": "..."} for one region.

        Cached on disk by content hash so repeated map renders are free.
        """
        cache_key = self._cache_key(region)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        user_msg = (
            "Region JSON payload:\n"
            f"{json.dumps(region, indent=2)}\n\n"
            "Respond with the JSON object only."
        )
        raw = self._chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=600,
            temperature=0.2,
        )
        parsed = self._parse_explanation(raw)
        self._cache_put(cache_key, parsed)
        return parsed

    def executive_summary(self, top_regions: list[dict]) -> dict:
        """Return {"paragraph": str, "themes": [str, str, str]} for the cluster."""
        cache_key = self._exec_cache_key(top_regions)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        user_msg = (
            "Ranked counties (top to bottom):\n"
            f"{json.dumps(top_regions, indent=2)}\n\n"
            "Respond with the JSON object only."
        )
        raw = self._chat(
            messages=[
                {"role": "system", "content": EXEC_SUMMARY_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=600,
            temperature=0.2,
        )
        parsed = self._parse_exec_summary(raw)
        self._cache_put(cache_key, parsed)
        return parsed

    def parse_search_query(self, query: str) -> dict:
        """Translate NL query to a filter dict. Returns {} if unparseable."""
        user_msg = f"Request: {query}\n\nRespond with the JSON object only."
        raw = self._chat(
            messages=[
                {"role": "system", "content": SEARCH_PARSE_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=300,
            temperature=0.0,
        )
        cleaned = _THINK_BLOCK.sub("", raw).strip()
        for c in self._json_candidates(cleaned):
            try:
                obj = json.loads(c)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
        return {}

    def explain_whatif(self, region_name: str, hypothetical: dict,
                       before: dict, after: dict, delta: dict) -> dict:
        """Return {"bullets": [...]} describing a hypothetical-alert scenario."""
        payload = {
            "region": region_name,
            "hypothetical_alert": hypothetical,
            "before": before,
            "after": after,
            "delta": delta,
        }
        user_msg = (
            f"Scenario payload:\n{json.dumps(payload, indent=2)}\n\n"
            "Respond with the JSON object only."
        )
        raw = self._chat(
            messages=[
                {"role": "system", "content": WHATIF_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=500,
            temperature=0.2,
        )
        cleaned = _THINK_BLOCK.sub("", raw).strip()
        for c in self._json_candidates(cleaned):
            try:
                obj = json.loads(c)
                bullets = obj.get("bullets") or []
                if isinstance(bullets, list) and bullets:
                    return {"bullets": [str(b).strip() for b in bullets[:5]]}
            except json.JSONDecodeError:
                continue
        return {"bullets": [cleaned[:300] or "(Model returned no parseable answer.)"]}

    @staticmethod
    def _json_candidates(cleaned: str) -> list[str]:
        out: list[str] = []
        fenced = _FENCED_JSON.search(cleaned)
        if fenced:
            out.append(fenced.group(1))
        if cleaned.startswith("{"):
            out.append(cleaned)
        first, last = cleaned.find("{"), cleaned.rfind("}")
        if 0 <= first < last:
            out.append(cleaned[first : last + 1])
        return out

    @classmethod
    def _parse_exec_summary(cls, raw: str) -> dict:
        cleaned = _THINK_BLOCK.sub("", raw).strip()
        for c in cls._json_candidates(cleaned):
            try:
                obj = json.loads(c)
            except json.JSONDecodeError:
                continue
            paragraph = str(obj.get("paragraph") or "").strip()
            themes = obj.get("themes") or []
            if paragraph:
                return {
                    "paragraph": paragraph,
                    "themes": [str(t).strip() for t in themes[:5]],
                }
        return {"paragraph": cleaned[:600] or "(no summary)", "themes": []}

    @staticmethod
    def _exec_cache_key(top: list[dict]) -> str:
        canonical = json.dumps(
            {
                "fips": [r.get("fips") for r in top],
                "dpis": [round(r.get("dpi", 0.0), 3) for r in top],
                "version": SCORING_VERSION,
                "kind": "exec_summary",
            },
            sort_keys=True,
        )
        return "summary-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]

    @staticmethod
    def _parse_explanation(raw: str) -> dict:
        cleaned = _THINK_BLOCK.sub("", raw).strip()
        candidates: list[str] = []
        fenced = _FENCED_JSON.search(cleaned)
        if fenced:
            candidates.append(fenced.group(1))
        if cleaned.startswith("{"):
            candidates.append(cleaned)
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if 0 <= first_brace < last_brace:
            candidates.append(cleaned[first_brace : last_brace + 1])

        for c in candidates:
            try:
                obj = json.loads(c)
            except json.JSONDecodeError:
                continue
            bullets = obj.get("bullets") or []
            stock = obj.get("stock_summary") or ""
            if isinstance(bullets, list) and bullets:
                return {
                    "bullets": [str(b).strip() for b in bullets[:5]],
                    "stock_summary": str(stock).strip(),
                }

        # Fallback: split visible lines into bullets so the UI never shows blank.
        lines = [ln.strip(" -*•\t") for ln in cleaned.splitlines() if ln.strip()]
        return {
            "bullets": lines[:5] or ["(Model returned no parseable explanation.)"],
            "stock_summary": "",
        }

    @staticmethod
    def _cache_key(region: dict) -> str:
        # Stable hash of the inputs that would change the answer.
        canonical = json.dumps(
            {
                "fips": region.get("fips"),
                "events": sorted(
                    [e.get("event", "") for e in region.get("forecast_events", [])]
                ),
                "dpi": round(region.get("demand_priority_index", 0.0), 4),
                "version": SCORING_VERSION,
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    @staticmethod
    def _cache_get(key: str) -> dict | None:
        path = CACHE_DIR / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _cache_put(key: str, value: dict) -> None:
        path = CACHE_DIR / f"{key}.json"
        try:
            path.write_text(json.dumps(value, indent=2))
        except OSError as e:
            logger.warning("Failed to cache explanation %s: %s", key, e)


def explain_region(region: dict) -> dict:
    """Module-level convenience for one-shot callers."""
    with LLMClient() as c:
        return c.explain_region(region)


if __name__ == "__main__":
    sample = {
        "region": "Miami-Dade County, FL",
        "fips": "12086",
        "forecast_events": [
            {"event": "Hurricane Watch", "severity": "Severe"},
            {"event": "Storm Surge Watch", "severity": "Severe"},
        ],
        "population": 2716940,
        "housing_exposure": {
            "older_housing_score": 0.62,
            "owner_occupied_units": 489001,
        },
        "nearby_home_depot_stores": 18,
        "risk_scores": {"hurricane": 0.91, "flood": 0.74},
        "demand_priority_index": 0.83,
        "score_breakdown": {
            "forecast_impact": 0.40,
            "pop_size": 0.18,
            "stock_urgency": 0.15,
            "housing_exposure": 0.06,
            "store_coverage_gap": 0.04,
        },
        "recommended_stock_categories": [
            "tarps",
            "plywood",
            "generators",
            "batteries",
            "sump pumps",
        ],
    }
    out = explain_region(sample)
    print(json.dumps(out, indent=2))
