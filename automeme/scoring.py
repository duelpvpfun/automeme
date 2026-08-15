"""Quality/virality scoring.

Blends several normalized signals into a single 0-100 ``quality_score`` that
the scheduler uses to pick what to post. Weights are nudged over time by the
learning module based on real engagement (see ``learning.py``).

Signals:
* velocity   -- growth rate at source (finds memes *before* they saturate)
* score      -- raw popularity at source (upvotes/likes)
* freshness  -- prefer recently created items
* taste      -- similarity to the account's learned style (@s8n exemplars)
* engagement -- learned prior: how well this source/subject has performed for us
"""

from __future__ import annotations

import math

from . import settings_store, taste
from .learning import source_subject_prior


def _norm_log(x: float, cap: float) -> float:
    if x <= 0:
        return 0.0
    return min(math.log1p(x) / math.log1p(cap), 1.0)


def default_weights() -> dict[str, float]:
    stored = settings_store.get("scoring_weights", None)
    if isinstance(stored, dict) and stored:
        return stored
    return {
        "velocity": 0.30,
        "score": 0.20,
        "freshness": 0.10,
        "taste": 0.25,
        "engagement": 0.15,
    }


def compute_quality(
    *,
    velocity: float,
    source_score: int,
    age_hours: float,
    phash: str,
    width: int,
    height: int,
    text_density: float,
    source: str,
    subject: str,
) -> tuple[float, dict]:
    w = default_weights()

    velocity_n = _norm_log(velocity, cap=5000.0)
    score_n = _norm_log(source_score, cap=100000.0)
    freshness_n = max(0.0, 1.0 - min(age_hours / 48.0, 1.0))
    taste_n = taste.taste_score(phash, width, height, text_density) / 100.0
    engagement_n = source_subject_prior(source, subject)  # 0..1

    parts = {
        "velocity": velocity_n,
        "score": score_n,
        "freshness": freshness_n,
        "taste": taste_n,
        "engagement": engagement_n,
    }
    total = sum(w.get(k, 0.0) * v for k, v in parts.items())
    quality = round(100.0 * total, 2)
    breakdown = {k: round(v, 3) for k, v in parts.items()} | {"weights": w}
    return quality, breakdown
