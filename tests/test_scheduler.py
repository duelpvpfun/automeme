"""Scheduler gates: limits, active hours, pause/kill, and dry-run posting."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from automeme import scheduler, settings_store
from automeme.db import session_scope
from automeme.models import Candidate, CandidateStatus


def _queue_one(quality=90.0, phash="a1a1a1a1a1a1a1a1"):
    with session_scope() as s:
        c = Candidate(
            source="fake", source_id=phash, image_url="http://x/y.png",
            local_path="/tmp/y.png", phash=phash, width=500, height=500,
            subject="memes", source_score=9000, quality_score=quality,
            safety_passed=True, status=CandidateStatus.QUEUED.value,
        )
        s.add(c)
        s.flush()
        return c.id


def test_no_post_when_paused(env):
    _queue_one()
    settings_store.update({"paused": True, "mode": settings_store.MODE_AUTO})
    assert scheduler.post_one() is False


def test_no_post_in_approval_mode(env):
    _queue_one()
    settings_store.update({"paused": False, "mode": settings_store.MODE_APPROVAL})
    assert scheduler.post_one() is False


def test_kill_switch_blocks_post(env):
    _queue_one()
    settings_store.update({"paused": False, "mode": settings_store.MODE_AUTO,
                           "kill_switch": True})
    assert scheduler.post_one() is False


def test_auto_post_dry_run(env, monkeypatch):
    cid = _queue_one()
    # Force active hours + no spacing constraints.
    monkeypatch.setattr(scheduler, "_within_active_hours", lambda: True)
    settings_store.update({
        "paused": False, "mode": settings_store.MODE_AUTO, "kill_switch": False,
        "min_minutes_between_posts": 0, "schedule_jitter_minutes": 0,
    })
    posted = scheduler.post_one()
    assert posted is True

    with session_scope() as s:
        c = s.get(Candidate, cid)
        assert c.status == CandidateStatus.POSTED.value
        assert c.x_post_id.startswith("dryrun-")


def test_daily_cap_enforced(env, monkeypatch):
    monkeypatch.setattr(scheduler, "_within_active_hours", lambda: True)
    settings_store.update({
        "paused": False, "mode": settings_store.MODE_AUTO, "kill_switch": False,
        "min_minutes_between_posts": 0, "schedule_jitter_minutes": 0,
        "posts_per_day": 1, "max_posts_per_day": 1,
    })
    _queue_one(phash="1111111111111111")
    _queue_one(phash="2222222222222222")
    assert scheduler.post_one() is True   # first ok
    assert scheduler.post_one() is False  # cap reached


def test_active_hours_gate(env, monkeypatch):
    _queue_one()
    monkeypatch.setattr(scheduler, "_within_active_hours", lambda: False)
    settings_store.update({"paused": False, "mode": settings_store.MODE_AUTO})
    assert scheduler.post_one() is False
