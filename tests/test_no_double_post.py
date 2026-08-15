"""Guard against the double-post bug: concurrent post_one must post at most once,
and the same candidate can never be posted twice."""

from __future__ import annotations

import threading

from automeme import scheduler, settings_store
from automeme.db import session_scope
from automeme.models import Candidate, CandidateStatus


def _queue(n: int) -> None:
    with session_scope() as s:
        for i in range(n):
            s.add(Candidate(
                source="memeapi", source_id=f"c{i}", subject="memes",
                image_url=f"http://x/{i}.png", local_path=f"/tmp/{i}.png",
                phash=f"{i:016x}", width=500, height=500, quality_score=90 - i,
                status=CandidateStatus.QUEUED.value,
            ))


def _go_live(monkeypatch):
    monkeypatch.setattr(scheduler, "_within_active_hours", lambda: True)
    settings_store.update({
        "mode": settings_store.MODE_AUTO, "paused": False, "kill_switch": False,
        "min_minutes_between_posts": 0, "schedule_jitter_minutes": 0,
        "alternate_meme_animal": False, "posts_per_day": 16, "max_posts_per_day": 16,
        "max_same_source_per_day": 99, "max_same_subject_per_day": 99,
        "dedup_hamming_threshold": 2,
    })


def test_concurrent_post_one_posts_at_most_once(env, monkeypatch):
    _go_live(monkeypatch)
    _queue(5)

    results: list[bool] = []
    lock = threading.Lock()

    def run():
        r = scheduler.post_one()
        with lock:
            results.append(r)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # At most one of the six concurrent calls should have posted.
    assert sum(1 for r in results if r) <= 1

    with session_scope() as s:
        from sqlalchemy import func, select
        posted = s.execute(
            select(func.count()).select_from(Candidate).where(
                Candidate.status == CandidateStatus.POSTED.value)
        ).scalar_one()
    assert posted <= 1


def test_same_candidate_never_posted_twice(env, monkeypatch):
    _go_live(monkeypatch)
    _queue(1)
    assert scheduler.post_one() is True
    # spacing is 0 but the item is already POSTED + remembered -> no second post
    assert scheduler.post_one() is False
    with session_scope() as s:
        from sqlalchemy import func, select
        posted = s.execute(
            select(func.count()).select_from(Candidate).where(
                Candidate.status == CandidateStatus.POSTED.value)
        ).scalar_one()
    assert posted == 1
