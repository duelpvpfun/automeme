"""Runtime, control-panel-editable settings, persisted in the DB.

Everything here can be changed live from the control panel without a restart.
Defaults are conservative and safe.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from .db import session_scope
from .models import Setting

# Operating modes
MODE_AUTO = "auto"                # discover -> safety -> queue -> auto post
MODE_APPROVAL = "approval"        # everything but a human must approve before posting

DEFAULTS: dict[str, Any] = {
    # Master switches. Default to fully autonomous; the real safety gate is
    # AUTOMEME_DRY_RUN (true by default) -- nothing posts for real until you set
    # DRY_RUN=false AND provide X credentials.
    "mode": MODE_AUTO,                 # auto = bot picks + posts by itself
    "paused": False,                   # running by default
    "kill_switch": False,              # hard stop; nothing posts, ever, while true

    # Posting cadence -- 16 posts/day, one every 90 min, around the clock.
    "posts_per_day": 16,               # target posts/day
    "max_posts_per_day": 20,           # hard ceiling (never exceeded)
    "min_minutes_between_posts": 90,   # 1.5h spacing
    "active_hours_start": 0,           # 24/7
    "active_hours_end": 24,            # 24/7
    "schedule_jitter_minutes": 20,     # +/- randomization for natural timing

    # Content policy
    "allowed_subjects": [],            # empty = allow all discovered subjects
    "min_quality_score": 45.0,         # 0-100
    "min_source_score": 200,           # minimum upvotes/score at source (catch early)
    # Freshness: reject anything older than this (hours). The whole point is to
    # catch NEW memes early -- before they spread to X -- not repost old virals.
    # Requires Reddit API creds for accurate age; meme-api age is estimated.
    "max_content_age_hours": 24,
    "caption_mode": "ai",              # none|ai|title  (ai = animal names + s8n lines)
    "max_caption_length": 0,           # 0 = never add a caption

    # Meme vs cute-animal mix
    "alternate_meme_animal": True,     # take turns between memes and animals
    "strict_alternate": True,          # never post 2 of the same type in a row
    "animal_name_caption": True,       # caption animal posts with the pet's name

    # Safety thresholds (higher = stricter)
    # medium (default): rule-based text screening + image sanity; usable without
    #   optional ML models. high: additionally REQUIRES the nudity detector +
    #   OCR to be installed and treats their absence as uncertain (=> reject).
    "safety_strictness": "medium",     # low|medium|high
    "reject_on_uncertainty": True,     # uncertain => reject (required by spec)
    "require_ocr": False,              # if true and OCR unavailable => reject text-heavy imgs
    "dedup_hamming_threshold": 6,      # <= => considered near-duplicate

    # Anti-spam / format diversity
    "max_same_source_per_day": 20,
    "max_same_subject_per_day": 8,
    "recent_format_window": 5,         # avoid repeating dominant format within N posts

    # Freshness of the QUEUE itself: a queued meme that never got posted within
    # this many hours is dropped (it's no longer fresh). Prevents a large backlog
    # from posting stale content days later. Keep small to stay timely.
    "queue_ttl_hours": 12,
    "max_queue_size": 40,              # cap the backlog; trim lowest-quality overflow

    # Resilience
    "max_consecutive_errors": 5,       # auto-shutdown (kill_switch) after this many
    "discovery_interval_minutes": 30,
    # Learning is not time-critical, so poll engagement sparingly to save API
    # credit: once a day, and only for posts younger than metrics_max_age_hours
    # (older posts barely change). This keeps read-call volume very low.
    "metrics_refresh_hours": 24,
    "metrics_max_age_hours": 48,

    # X discovery (pay-per-use: reads cost credit). Off unless enabled here AND
    # read credentials exist. max_results caps credit spend per cycle.
    "x_discovery_enabled": False,
    "x_discovery_max_results": 25,     # 10..100 tweets per search request
}


def _coerce(key: str, raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return DEFAULTS.get(key)


def get_all() -> dict[str, Any]:
    values = dict(DEFAULTS)
    with session_scope() as s:
        for row in s.execute(select(Setting)).scalars():
            values[row.key] = _coerce(row.key, row.value)
    return values


def get(key: str, default: Any = None) -> Any:
    with session_scope() as s:
        row = s.get(Setting, key)
        if row is None:
            return DEFAULTS.get(key, default)
        return _coerce(key, row.value)


def set_value(key: str, value: Any) -> None:
    with session_scope() as s:
        row = s.get(Setting, key)
        payload = json.dumps(value)
        if row is None:
            s.add(Setting(key=key, value=payload))
        else:
            row.value = payload


def update(values: dict[str, Any]) -> None:
    for k, v in values.items():
        set_value(k, v)


def ensure_defaults() -> None:
    """Seed any missing settings with their defaults."""
    with session_scope() as s:
        existing = {r.key for r in s.execute(select(Setting)).scalars()}
        for k, v in DEFAULTS.items():
            if k not in existing:
                s.add(Setting(key=k, value=json.dumps(v)))
