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
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

# Serialize posting: boot cycle, the 5-min tick, and the manual "Post now" button
# must never post concurrently (that caused the same meme to go out twice).
_post_lock = threading.Lock()

from . import captioning, categories, dedup, learning, pipeline, settings_store
from .activity import log
from .db import session_scope
from .models import Candidate, CandidateStatus, DailyStat
from .publishing import PublishError, PublishUncertainError, get_client
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

    # Count ACTUAL posts today (source of truth), not just the daily counter,
    # so counter drift can never wrongly block or over-post.
    posted_today = _posts_today()
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


def _posts_today() -> int:
    from sqlalchemy import func
    with session_scope() as s:
        return int(
            s.execute(
                select(func.count()).select_from(Candidate).where(
                    Candidate.status == CandidateStatus.POSTED.value,
                    Candidate.posted_at.isnot(None),
                    func.date(Candidate.posted_at) == _now().strftime("%Y-%m-%d"),
                )
            ).scalar_one()
        )


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

    ttl_h = float(settings_store.get("queue_ttl_hours", 12))
    ttl_cutoff = _now() - timedelta(hours=ttl_h)

    with session_scope() as s:
        rows = list(
            s.execute(
                select(Candidate)
                .where(Candidate.status == CandidateStatus.QUEUED.value)
                # Best-quality first. The queue is already kept fresh by the TTL
                # + trim, so within that fresh pool we always post the BEST one.
                .order_by(Candidate.quality_score.desc())
                .limit(120)
            ).scalars()
        )

        def fresh(c: Candidate) -> bool:
            created = c.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return not (created and created < ttl_cutoff)

        def eligible(c: Candidate) -> bool:
            # Diversity caps are a soft preference: they must never make the bot
            # go completely silent, so if NOTHING passes them we relax below.
            return fresh(c) and (
                _get_count("source", c.source) < max_src
                and _get_count("subject", c.subject) < max_subj
            )

        # If diversity caps would block the entire fresh queue, ignore them for
        # this pick (better to post a good meme than nothing).
        if not any(eligible(c) for c in rows):
            eligible = fresh  # noqa: F811 -- intentional relaxation

        def claim(c: Candidate) -> Candidate | None:
            # Atomic, cross-process claim: only flip the row if it is STILL
            # queued. rowcount==1 means we won; 0 means another worker already
            # took it -> skip. This is the true guard against double-posting.
            from sqlalchemy import update
            res = s.execute(
                update(Candidate)
                .where(Candidate.id == c.id,
                       Candidate.status == CandidateStatus.QUEUED.value)
                .values(status=CandidateStatus.POSTING.value)
            )
            if res.rowcount != 1:
                return None
            s.flush()
            s.refresh(c)
            s.expunge(c)
            return c

        def pick_from(pool: list[Candidate]) -> Candidate | None:
            """Weighted-random among candidates within `pick_score_band` points
            of the best -- so it's not always the exact same #1 meme every time
            (e.g. 89/86/83/82/80 all count as "great" and any can be chosen),
            while still strongly favoring higher scores."""
            if not pool:
                return None
            band = float(settings_store.get("pick_score_band", 8.0))
            best = pool[0].quality_score
            contenders = [c for c in pool if best - c.quality_score <= band] or pool[:1]
            weights = [max(c.quality_score, 0.1) for c in contenders]
            order = list(range(len(contenders)))
            random.shuffle(order)  # randomize tie order before weighting
            total_w = sum(weights)
            r = random.uniform(0, total_w)
            upto = 0.0
            for i in order:
                upto += weights[i]
                if upto >= r:
                    won = claim(contenders[i])
                    if won is not None:
                        return won
            # Fallback (floating point edge case): try them all in order.
            for c in contenders:
                won = claim(c)
                if won is not None:
                    return won
            return None

        # Pass 1: prefer the opposite category (nice alternation); pick among
        # the near-top of that category, weighted-random -- not always #1.
        if preferred is not None:
            pool = [c for c in rows if eligible(c)
                    and categories.category_for(c.subject) == preferred]
            won = pick_from(pool)
            if won is not None:
                return won

        # Pass 2: preferred category has nothing ready -> pick among the
        # near-top of ANY category. We never stay silent just to keep a
        # perfect alternation pattern.
        pool = [c for c in rows if eligible(c)]
        won = pick_from(pool)
        if won is not None:
            return won
    return None


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def post_one() -> bool:
    """Publish a single best candidate if all gates pass. Returns True if posted.

    Fully serialized via ``_post_lock`` so concurrent callers (boot cycle, the
    scheduled tick, the manual button) can never post two memes at once.
    """
    if not _post_lock.acquire(blocking=False):
        # Another post is already in progress -- never run two at once.
        return False
    try:
        ok, reason = _should_post_now()
        if not ok:
            return False

        cand = _pick_candidate()  # atomically claims the row as POSTING
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

        # RESERVE the pHash in permanent posted-memory BEFORE sending the tweet.
        # The UNIQUE constraint means if this image was ever posted (or is being
        # posted concurrently, even in another process/replica), the reservation
        # fails and we abort -- making a duplicate post physically impossible.
        if not dedup.reserve_posted(cand.phash, cand.id):
            _mark(cand.id, CandidateStatus.DUPLICATE.value,
                  "phash already reserved/posted")
            return False

        caption = _build_caption(cand)
        try:
            result = get_client().post_image(cand.local_path, caption=caption)
        except PublishUncertainError as exc:
            # We CANNOT tell if the tweet actually went out. Do NOT release the
            # dedup reservation and do NOT requeue -- the only safe move is to
            # retire this image permanently rather than risk a real duplicate.
            _mark(cand.id, CandidateStatus.FAILED.value,
                  f"uncertain publish result (not retried): {exc}")
            _record_error("post_one", exc)
            return False
        except PublishError as exc:
            # Nothing was sent to X (e.g. media upload failed) -> safe to retry.
            dedup.release_posted(cand.phash)
            _mark(cand.id, CandidateStatus.QUEUED.value, f"publish failed: {exc}")
            _record_error("post_one", exc)
            return False

        with session_scope() as s:
            c = s.get(Candidate, cand.id)
            c.status = CandidateStatus.POSTED.value
            c.posted_at = _now()
            c.x_post_id = result.post_id
            c.caption = caption
        # Free disk: the image is uploaded, and dedup only needs the pHash (in
        # DB), so the local file is no longer needed.
        _delete_image(cand.local_path)
        _bump("total")
        _bump("source", cand.source)
        _bump("subject", cand.subject)
        _record_success()
        log("posted",
            f"id={cand.id} post_id={result.post_id} dry_run={result.dry_run}",
            candidate_id=cand.id)
        return True
    finally:
        _post_lock.release()


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


def trim_queue() -> None:
    """Drop stale queued items and cap the backlog so posts stay fresh.

    * Anything queued longer than ``queue_ttl_hours`` is disabled (too old now).
    * If the queue still exceeds ``max_queue_size``, the lowest-quality overflow
      is disabled, keeping only the best, freshest candidates.
    """
    ttl_h = float(settings_store.get("queue_ttl_hours", 12))
    max_size = int(settings_store.get("max_queue_size", 40))
    cutoff = _now() - timedelta(hours=ttl_h)
    expired = trimmed = 0
    with session_scope() as s:
        rows = list(
            s.execute(
                select(Candidate)
                .where(Candidate.status == CandidateStatus.QUEUED.value)
                .order_by(Candidate.quality_score.desc())
            ).scalars()
        )
        kept = []
        for c in rows:
            created = c.created_at
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created and created < cutoff:
                c.status = CandidateStatus.DISABLED.value
                c.status_reason = f"expired from queue (>{ttl_h}h old, stale)"
                expired += 1
            else:
                kept.append(c)
        for c in kept[max_size:]:
            c.status = CandidateStatus.DISABLED.value
            c.status_reason = "queue trimmed (backlog cap; lower quality)"
            trimmed += 1
    if expired or trimmed:
        log("queue_trimmed", f"expired={expired} trimmed={trimmed}")


def _delete_image(path: str) -> None:
    if not path:
        return
    try:
        from pathlib import Path
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def cleanup_images() -> None:
    """Delete image files that are no longer needed and prune orphan files.

    Keeps only images for candidates still QUEUED/AWAITING (they haven't posted
    yet). Everything else -- posted, rejected, disabled, duplicate -- has its
    file removed. Also deletes any file on disk with no matching candidate.
    """
    from pathlib import Path

    keep_statuses = {
        CandidateStatus.QUEUED.value,
        CandidateStatus.AWAITING_APPROVAL.value,
        CandidateStatus.POSTING.value,
        CandidateStatus.SCORED.value,
        CandidateStatus.DISCOVERED.value,
    }
    cfg = get_client().cfg
    freed = 0
    keep_paths: set[str] = set()
    with session_scope() as s:
        for c in s.execute(select(Candidate)).scalars():
            if c.status in keep_statuses and c.local_path:
                keep_paths.add(c.local_path)
            elif c.local_path:
                if Path(c.local_path).exists():
                    _delete_image(c.local_path)
                    freed += 1
                c.local_path = ""
    # Prune orphan files (e.g. left by crashes) not referenced by any keeper.
    try:
        for f in cfg.images_path.iterdir():
            if f.is_file() and str(f) not in keep_paths:
                f.unlink(missing_ok=True)
                freed += 1
    except OSError:
        pass
    if freed:
        log("images_cleaned", f"removed {freed} image files")


def run_discovery() -> None:
    try:
        pipeline.ingest()
        trim_queue()
        cleanup_images()
        _record_success()
    except Exception as exc:  # noqa: BLE001
        _record_error("run_discovery", exc)


def refresh_metrics() -> None:
    """Pull fresh engagement for recently posted items and learn from it.

    Only polls posts younger than ``metrics_max_age_hours`` -- older posts have
    essentially stopped changing, so re-reading them just wastes API credit.
    """
    client = get_client()
    max_age_h = float(settings_store.get("metrics_max_age_hours", 48))
    cutoff = _now() - timedelta(hours=max_age_h)
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


def recover_stranded() -> None:
    """Requeue candidates stuck in POSTING (e.g. from a crash/redeploy mid-post),
    so the queue never silently leaks."""
    n = 0
    with session_scope() as s:
        for c in s.execute(
            select(Candidate).where(Candidate.status == CandidateStatus.POSTING.value)
        ).scalars():
            c.status = CandidateStatus.QUEUED.value
            c.status_reason = "recovered from interrupted post"
            n += 1
    if n:
        log("recovered_stranded", f"requeued {n} interrupted candidates",
            level="warning")


def start() -> None:
    global _scheduler
    if not _APS_OK:
        log("scheduler_unavailable", "apscheduler not installed", level="warning")
        return
    if _scheduler is not None:
        return
    recover_stranded()
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
