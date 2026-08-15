"""Queue freshness: stale entries expire, backlog is capped, freshest posts first."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from automeme import scheduler, settings_store
from automeme.db import session_scope
from automeme.models import Candidate, CandidateStatus


def _add(cid_seed, hours_old, quality, subject="memes"):
    with session_scope() as s:
        c = Candidate(
            source="memeapi", source_id=str(cid_seed), subject=subject,
            image_url=f"http://x/{cid_seed}.png", local_path=f"/tmp/{cid_seed}.png",
            phash=f"{cid_seed:016x}", width=500, height=500, quality_score=quality,
            status=CandidateStatus.QUEUED.value,
            created_at=datetime.now(timezone.utc) - timedelta(hours=hours_old),
        )
        s.add(c); s.flush()
        return c.id


def test_stale_queue_entries_expire(env):
    settings_store.update({"queue_ttl_hours": 12, "max_queue_size": 100})
    fresh = _add(1, hours_old=2, quality=80)
    stale = _add(2, hours_old=30, quality=95)  # high quality but too old
    scheduler.trim_queue()
    with session_scope() as s:
        assert s.get(Candidate, fresh).status == CandidateStatus.QUEUED.value
        assert s.get(Candidate, stale).status == CandidateStatus.DISABLED.value


def test_backlog_capped_by_quality(env):
    settings_store.update({"queue_ttl_hours": 999, "max_queue_size": 2})
    ids = [_add(10 + i, hours_old=1, quality=50 + i) for i in range(5)]
    scheduler.trim_queue()
    with session_scope() as s:
        kept = [i for i in ids if s.get(Candidate, i).status == CandidateStatus.QUEUED.value]
    assert len(kept) == 2  # only the 2 highest-quality survive


def test_picker_skips_stale(env, monkeypatch):
    settings_store.update({"queue_ttl_hours": 12, "alternate_meme_animal": False,
                           "max_same_source_per_day": 99, "max_same_subject_per_day": 99})
    _add(1, hours_old=30, quality=99)  # stale, should be skipped
    fresh = _add(2, hours_old=1, quality=60)
    picked = scheduler._pick_candidate()
    assert picked is not None and picked.id == fresh
