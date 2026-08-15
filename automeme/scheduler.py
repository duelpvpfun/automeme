"""Autonomous scheduler.

Responsibilities:

* periodically run discovery (``pipeline.ingest``),
* decide when it is time to post (respecting caps, spacing, active hours,
  randomized jitter, per-source/subject diversity),
* pick the best QUEUED candidate and publish it (auto mode only),
* refresh engagement metrics for recent posts,
* enforce resilience: count consecutive errors and trip the kill switch.

All posting rules are re-read from ``settings_store`` on every tick, so the
control panel can change behavior live.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import captioning, categories, dedup, learning, pipeline, settings_store
from .activity import log
from .db import session_scope
from .models import Candidate, CandidateStatus, DailyStat
from .publishing import PublishError, get_client
from .safety import evaluate, evaluate_text
from .safety.base import SafetyContext

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    _APS_OK = True
except Exception:  # pragma: no cover
    _APS_OK = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_key() -> str:
    return _now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Daily counters
# ---------------------------------------------------------------------------


def _get_count(kind: str, value: str = "") -> int:
    with session_scope() as s:
        row = s.execute(
            select(DailyStat).where(
                DailyStat.day == _today_key(),
                DailyStat.kind == kind,
                DailyStat.value == value,
            )
        ).scalar_one_or_none()
        return row.count if row else 0


def _bump(kind: str, value: str = "") -> None:
    with session_scope() as s:
        row = s.execute(
            select(DailyStat).where(
                DailyStat.day == _today_key(),
                DailyStat.kind == kind,
                DailyStat.value == value,
            )
        ).scalar_one_or_none()
        if row is None:
            s.add(DailyStat(day=_today_key(), kind=kind, value=value, count=1))
        else:
            row.count += 1


# ---------------------------------------------------------------------------
# Error / kill-switch bookkeeping
# ---------------------------------------------------------------------------

_consecutive_errors = 0


def _record_error(where: str, exc: Exception) -> None:
    global _consecutive_errors
    _consecutive_errors += 1
    log("error", f"{where}: {exc} (streak={_consecutive_errors})", level="error")
    limit = int(settings_store.get("max_consecutive_errors", 5))
    if _consecutive_errors >= limit:
        settings_store.set_value("kill_switch", True)
        settings_store.set_value("paused", True)
        log("auto_shutdown",
            f"kill switch engaged after {_consecutive_errors} consecutive errors",
            level="error")


def _record_success() -> None:
    global _consecutive_errors
    _consecutive_errors = 0


# ---------------------------------------------------------------------------
# Posting gates
# ---------------------------------------------------------------------------


def _within_active_hours() -> bool:
    start = int(settings_store.get("active_hours_start", 8))
    end = int(settings_store.get("active_hours_end", 23))
    hour = _now().hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps midnight


def _last_post_time() -> datetime | None:
    with session_scope() as s:
        row = s.execute(
            select(Candidate.posted_at)
            .where(Candidate.status == CandidateStatus.POSTED.value)
            .order_by(Candidate.posted_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row and row.tzinfo is None:
            return row.replace(tzinfo=timezone.utc)
        return row


def _should_post_now() -> tuple[bool, str]:
    if settings_store.get("kill_switch", False):
        return False, "kill switch active"
    if settings_store.get("paused", True):
        return False, "paused"
    if settings_store.get("mode") != settings_store.MODE_AUTO:
        return False, "not in auto mode"
    if not get_client().cfg.dry_run and not get_client().cfg.has_x_write_credentials:
        return False, "no X write credentials"
    if not _within_active_hours():
        return False, "outside active hours"

    posted_today = _get_count("total")
    if posted_today >= int(settings_store.get("max_posts_per_day", 12)):
        return False, "daily hard cap reached"
    if posted_today >= int(settings_store.get("posts_per_day", 8)):
        return False, "daily target reached"

    last = _last_post_time()
    if last is not None:
        gap_min = (_now() - last).total_seconds() / 60.0
        min_gap = int(settings_store.get("min_minutes_between_posts", 45))
        jitter = int(settings_store.get("schedule_jitter_minutes", 25))
        required = min_gap + random.randint(0, max(jitter, 0))
        if gap_min < required:
            return False, f"spacing: {gap_min:.0f}m < {required}m"
    return True, "ok"


def _last_posted_category() -> str | None:
    with session_scope() as s:
        c = s.execute(
            select(Candidate.subject)
            .where(Candidate.status == CandidateStatus.POSTED.value)
            .order_by(Candidate.posted_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return categories.category_for(c) if c is not None else None


def _pick_candidate() -> Candidate | None:
    max_src = int(settings_store.get("max_same_source_per_day", 4))
    max_subj = int(settings_store.get("max_same_subject_per_day", 4))

    # When alternation is on, prefer the category opposite the last post so memes
    # and cute animals take turns (each type lands on its own cadence).
    preferred: str | None = None
    if settings_store.get("alternate_meme_animal", True):
        last = _last_posted_category()
        if last == categories.MEME:
            preferred = categories.ANIMAL
        elif last == categories.ANIMAL:
            preferred = categories.MEME

    with session_scope() as s:
        rows = list(
            s.execute(
                select(Candidate)
                .where(Candidate.status == CandidateStatus.QUEUED.value)
                .order_by(Candidate.quality_score.desc())
                .limit(80)
            ).scalars()
        )

        def eligible(c: Candidate) -> bool:
            return (
                _get_count("source", c.source) < max_src
                and _get_count("subject", c.subject) < max_subj
            )

        # First pass: honor the preferred category. Second pass: any category
        # (so we still post if only one type is currently available).
        for want_preferred in (True, False):
            for c in rows:
                if not eligible(c):
                    continue
                if want_preferred and preferred is not None:
                    if categories.category_for(c.subject) != preferred:
                        continue
                s.expunge(c)
                return c
            if preferred is None:
                break
    return None


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def post_one() -> bool:
    """Publish a single best candidate if all gates pass. Returns True if posted."""
    ok, reason = _should_post_now()
    if not ok:
        return False

    cand = _pick_candidate()
    if cand is None:
        return False

    # Final re-checks immediately before posting (defense in depth).
    dup = dedup.find_duplicate(cand.phash, exclude_id=cand.id)
    if dup:
        _mark(cand.id, CandidateStatus.DUPLICATE.value, dup)
        return False

    ctx = SafetyContext(
        title=cand.title, ocr_text=cand.ocr_text, ocr_available=bool(cand.ocr_text),
        author=cand.author, subject=cand.subject, source=cand.source,
        image_url=cand.image_url, local_path=cand.local_path,
        width=cand.width, height=cand.height,
    )
    decision = evaluate(ctx)
    if not decision.passed:
        _mark(cand.id, CandidateStatus.SAFETY_REJECTED.value,
              "; ".join(decision.reasons)[:500])
        return False

    caption = _build_caption(cand)
    _mark(cand.id, CandidateStatus.POSTING.value, "")
    try:
        result = get_client().post_image(cand.local_path, caption=caption)
    except PublishError as exc:
        _mark(cand.id, CandidateStatus.QUEUED.value, f"publish failed: {exc}")
        _record_error("post_one", exc)
        return False

    with session_scope() as s:
        c = s.get(Candidate, cand.id)
        c.status = CandidateStatus.POSTED.value
        c.posted_at = _now()
        c.x_post_id = result.post_id
        c.caption = caption
    dedup.remember_posted(cand.phash, cand.id)
    _bump("total")
    _bump("source", cand.source)
    _bump("subject", cand.subject)
    _record_success()
    log("posted", f"id={cand.id} post_id={result.post_id} dry_run={result.dry_run}",
        candidate_id=cand.id)
    return True


def _build_caption(cand: Candidate) -> str:
    """Build a caption and safety-screen it. Falls back to '' (image-only) if
    the generated caption is empty or fails any safety check."""
    caption = captioning.generate(cand)
    if not caption:
        return ""
    # A caption is published text -> screen it through the text safety checks.
    if not evaluate_text(caption).passed:
        log("caption_rejected",
            f"candidate={cand.id} caption failed safety; posting image only",
            candidate_id=cand.id)
        return ""
    return caption


def _mark(candidate_id: int, status: str, reason: str) -> None:
    with session_scope() as s:
        c = s.get(Candidate, candidate_id)
        if c:
            c.status = status
            c.status_reason = reason


def run_discovery() -> None:
    try:
        pipeline.ingest()
        _record_success()
    except Exception as exc:  # noqa: BLE001
        _record_error("run_discovery", exc)


def refresh_metrics() -> None:
    """Pull fresh engagement for recently posted items and learn from it."""
    client = get_client()
    cutoff = _now() - timedelta(days=7)
    with session_scope() as s:
        rows = list(
            s.execute(
                select(Candidate).where(
                    Candidate.status == CandidateStatus.POSTED.value,
                    Candidate.posted_at.isnot(None),
                )
            ).scalars()
        )
        ids = [(c.id, c.x_post_id, c.posted_at) for c in rows]
    for cid, post_id, posted_at in ids:
        if posted_at and posted_at.replace(tzinfo=timezone.utc) < cutoff:
            continue
        pm = client.fetch_metrics(post_id)
        if pm is None:
            continue
        learning.apply_metrics(
            cid, impressions=pm.impressions, likes=pm.likes, reposts=pm.reposts,
            bookmarks=pm.bookmarks, replies=pm.replies,
        )


# ---------------------------------------------------------------------------
# Scheduler lifecycle
# ---------------------------------------------------------------------------

_scheduler = None


def start() -> None:
    global _scheduler
    if not _APS_OK:
        log("scheduler_unavailable", "apscheduler not installed", level="warning")
        return
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")

    disc_min = int(settings_store.get("discovery_interval_minutes", 30))
    metrics_hr = int(settings_store.get("metrics_refresh_hours", 6))

    _scheduler.add_job(run_discovery, IntervalTrigger(minutes=disc_min),
                       id="discovery", replace_existing=True, max_instances=1)
    # Check posting frequently; gates decide whether to actually post.
    _scheduler.add_job(_tick_post, IntervalTrigger(minutes=5),
                       id="post", replace_existing=True, max_instances=1)
    _scheduler.add_job(refresh_metrics, IntervalTrigger(hours=metrics_hr),
                       id="metrics", replace_existing=True, max_instances=1)
    _scheduler.start()
    log("scheduler_started",
        f"discovery={disc_min}m post=5m metrics={metrics_hr}h")


def _tick_post() -> None:
    try:
        post_one()
    except Exception as exc:  # noqa: BLE001
        _record_error("tick_post", exc)


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log("scheduler_stopped", "")


def reset_error_streak() -> None:
    global _consecutive_errors
    _consecutive_errors = 0
