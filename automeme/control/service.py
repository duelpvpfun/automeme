"""Business logic for the control panel, kept separate from HTTP wiring.

These functions are what the routes call. They are also convenient to unit
test directly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc, func, select

from .. import settings_store, taste
from ..activity import log, recent
from ..db import session_scope
from ..models import Blocklist, Candidate, CandidateStatus
from ..publishing import get_client


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- dashboard counts --------------------------------------------------------


def dashboard_stats() -> dict:
    with session_scope() as s:
        def count(status: str) -> int:
            return s.execute(
                select(func.count()).select_from(Candidate).where(
                    Candidate.status == status
                )
            ).scalar_one()

        posted_today = s.execute(
            select(func.count()).select_from(Candidate).where(
                Candidate.status == CandidateStatus.POSTED.value,
                Candidate.posted_at.isnot(None),
                func.date(Candidate.posted_at) == _now().strftime("%Y-%m-%d"),
            )
        ).scalar_one()

    st = settings_store.get_all()
    return {
        "mode": st["mode"],
        "paused": st["paused"],
        "kill_switch": st["kill_switch"],
        "dry_run": get_client().cfg.dry_run,
        "has_x_credentials": get_client().cfg.has_x_write_credentials,
        "queued": count(CandidateStatus.QUEUED.value),
        "awaiting": count(CandidateStatus.AWAITING_APPROVAL.value),
        "posted": count(CandidateStatus.POSTED.value),
        "safety_rejected": count(CandidateStatus.SAFETY_REJECTED.value),
        "posted_today": posted_today,
        "posts_per_day": st["posts_per_day"],
        "max_posts_per_day": st["max_posts_per_day"],
        "taste_exemplars": taste.exemplar_count(),
    }


def diagnose() -> dict:
    """Explain, in one call, why it is or isn't posting right now."""
    from .. import scheduler
    ok, reason = scheduler._should_post_now()
    with session_scope() as s:
        queued = s.execute(
            select(func.count()).select_from(Candidate).where(
                Candidate.status == CandidateStatus.QUEUED.value
            )
        ).scalar_one()
    return {
        "can_post_now": ok,
        "reason": reason,
        "queued": queued,
        "hint": (
            "Queue is empty -- discovery hasn't produced postable candidates yet."
            if queued == 0 else
            ("Ready -- next 5-min tick should post." if ok else f"Blocked: {reason}")
        ),
    }


def force_discovery() -> dict:
    """Run one discovery cycle right now (manual trigger)."""
    from .. import pipeline
    return pipeline.ingest()


def force_post() -> dict:
    """Attempt to post one right now, bypassing spacing (manual trigger)."""
    from .. import scheduler
    posted = scheduler.post_one()
    return {"posted": posted, **diagnose()}


# -- candidate listings ------------------------------------------------------


def list_candidates(status: str | None = None, limit: int = 100) -> list[dict]:
    with session_scope() as s:
        stmt = select(Candidate).order_by(desc(Candidate.created_at)).limit(limit)
        if status:
            stmt = (
                select(Candidate)
                .where(Candidate.status == status)
                .order_by(desc(Candidate.quality_score))
                .limit(limit)
            )
        rows = list(s.execute(stmt).scalars())
        return [_candidate_dict(c) for c in rows]


def _candidate_dict(c: Candidate) -> dict:
    return {
        "id": c.id,
        "source": c.source,
        "subject": c.subject,
        "title": c.title,
        "image_url": c.image_url,
        "source_url": c.source_url,
        "quality_score": c.quality_score,
        "status": c.status,
        "status_reason": c.status_reason,
        "source_score": c.source_score,
        "velocity": round(c.velocity, 1),
        "posted_at": c.posted_at.isoformat() if c.posted_at else None,
        "x_post_id": c.x_post_id,
        "engagement_rate": c.engagement_rate,
        "metric_impressions": c.metric_impressions,
        "metric_likes": c.metric_likes,
        "metric_reposts": c.metric_reposts,
        "metric_bookmarks": c.metric_bookmarks,
        "width": c.width,
        "height": c.height,
    }


# -- queue actions -----------------------------------------------------------


def approve(candidate_id: int) -> bool:
    with session_scope() as s:
        c = s.get(Candidate, candidate_id)
        if not c or c.status not in (
            CandidateStatus.AWAITING_APPROVAL.value, CandidateStatus.QUEUED.value
        ):
            return False
        c.status = CandidateStatus.QUEUED.value
        c.status_reason = "approved by operator"
    log("approved", f"candidate={candidate_id}", candidate_id=candidate_id)
    return True


def reject(candidate_id: int) -> bool:
    with session_scope() as s:
        c = s.get(Candidate, candidate_id)
        if not c:
            return False
        c.status = CandidateStatus.REJECTED.value
        c.status_reason = "rejected by operator"
    log("rejected", f"candidate={candidate_id}", candidate_id=candidate_id)
    return True


def disable(candidate_id: int) -> bool:
    """Immediately remove a queued item so it can never post."""
    with session_scope() as s:
        c = s.get(Candidate, candidate_id)
        if not c:
            return False
        c.status = CandidateStatus.DISABLED.value
        c.status_reason = "disabled by operator"
    log("disabled", f"candidate={candidate_id}", candidate_id=candidate_id)
    return True


def purge_queue() -> int:
    """Emergency: disable everything currently queued/awaiting."""
    n = 0
    with session_scope() as s:
        rows = list(
            s.execute(
                select(Candidate).where(
                    Candidate.status.in_([
                        CandidateStatus.QUEUED.value,
                        CandidateStatus.AWAITING_APPROVAL.value,
                    ])
                )
            ).scalars()
        )
        for c in rows:
            c.status = CandidateStatus.DISABLED.value
            c.status_reason = "queue purged by operator"
            n += 1
    log("queue_purged", f"count={n}", level="warning")
    return n


def delete_posted(candidate_id: int) -> bool:
    """Delete a live post from X (and mark it disabled locally)."""
    with session_scope() as s:
        c = s.get(Candidate, candidate_id)
        if not c or c.status != CandidateStatus.POSTED.value:
            return False
        post_id = c.x_post_id
    ok = get_client().delete_post(post_id)
    if ok:
        with session_scope() as s:
            c = s.get(Candidate, candidate_id)
            c.status = CandidateStatus.DISABLED.value
            c.status_reason = "deleted from X by operator"
        log("post_deleted", f"candidate={candidate_id}", candidate_id=candidate_id)
    return ok


# -- master switches ---------------------------------------------------------


def set_paused(paused: bool) -> None:
    settings_store.set_value("paused", paused)
    log("pause_toggled", f"paused={paused}", level="warning")


def set_kill_switch(active: bool) -> None:
    settings_store.set_value("kill_switch", active)
    if active:
        settings_store.set_value("paused", True)
    log("kill_switch_toggled", f"active={active}", level="warning")


def set_mode(mode: str) -> bool:
    if mode not in (settings_store.MODE_AUTO, settings_store.MODE_APPROVAL):
        return False
    settings_store.set_value("mode", mode)
    log("mode_changed", f"mode={mode}", level="warning")
    return True


# -- settings ----------------------------------------------------------------

_ALLOWED_SETTING_KEYS = set(settings_store.DEFAULTS.keys()) | {"scoring_weights"}


def update_settings(values: dict) -> dict:
    """Validate + persist a subset of runtime settings."""
    clean: dict = {}
    for k, v in values.items():
        if k not in _ALLOWED_SETTING_KEYS:
            continue
        clean[k] = v
    _sanitize(clean)
    settings_store.update(clean)
    log("settings_updated", ", ".join(sorted(clean.keys())))
    return settings_store.get_all()


def _sanitize(d: dict) -> None:
    # Enforce hard bounds so the panel can't create unsafe configurations.
    if "max_posts_per_day" in d:
        d["max_posts_per_day"] = max(1, min(int(d["max_posts_per_day"]), 24))
    if "posts_per_day" in d:
        d["posts_per_day"] = max(1, min(int(d["posts_per_day"]), 24))
    if "min_minutes_between_posts" in d:
        d["min_minutes_between_posts"] = max(15, int(d["min_minutes_between_posts"]))
    if "safety_strictness" in d and d["safety_strictness"] not in ("low", "medium", "high"):
        d["safety_strictness"] = "high"
    if "caption_mode" in d and d["caption_mode"] not in ("none", "title"):
        d["caption_mode"] = "none"


# -- blocklist ---------------------------------------------------------------


def list_blocklist() -> list[dict]:
    with session_scope() as s:
        rows = list(s.execute(select(Blocklist).order_by(Blocklist.id.desc())).scalars())
        return [{"id": r.id, "kind": r.kind, "value": r.value} for r in rows]


def add_block(kind: str, value: str) -> bool:
    kind = kind.strip().lower()
    value = value.strip()
    if kind not in ("source", "topic", "author", "phrase") or not value:
        return False
    with session_scope() as s:
        exists = s.execute(
            select(Blocklist).where(Blocklist.kind == kind, Blocklist.value == value)
        ).scalar_one_or_none()
        if exists:
            return True
        s.add(Blocklist(kind=kind, value=value))
    log("block_added", f"{kind}:{value}")
    return True


def remove_block(block_id: int) -> bool:
    with session_scope() as s:
        row = s.get(Blocklist, block_id)
        if not row:
            return False
        s.delete(row)
    log("block_removed", f"id={block_id}")
    return True


# -- taste -------------------------------------------------------------------


def add_taste_exemplar(image_url: str, label: str = "s8n") -> bool:
    return taste.add_exemplar(image_url.strip(), label=label.strip() or "s8n") is not None


# -- activity ----------------------------------------------------------------


def activity(limit: int = 200) -> list[dict]:
    rows = recent(limit=limit)
    return [
        {
            "ts": r.ts.isoformat() if r.ts else "",
            "level": r.level,
            "event": r.event,
            "candidate_id": r.candidate_id,
            "message": r.message,
        }
        for r in rows
    ]
