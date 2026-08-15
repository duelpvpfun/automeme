"""Dry-run simulation + text-only safety screening."""

from __future__ import annotations

from automeme import settings_store, simulate
from automeme.db import session_scope
from automeme.models import Candidate, CandidateStatus
from automeme.safety import evaluate_text


def _seed(n_memes=6, n_animals=6):
    with session_scope() as s:
        for i in range(n_memes):
            s.add(Candidate(source="memeapi", source_id=f"m{i}", subject="memes",
                            title="relatable content", image_url=f"http://x/m{i}.png",
                            local_path=f"/tmp/m{i}.png", phash=f"m{i:016x}",
                            width=500, height=500, quality_score=90 - i,
                            status=CandidateStatus.QUEUED.value))
        for i in range(n_animals):
            s.add(Candidate(source="memeapi", source_id=f"a{i}", subject="aww",
                            title=f"This is Rex{i}, my new pup", image_url=f"http://x/a{i}.png",
                            local_path=f"/tmp/a{i}.png", phash=f"a{i:016x}",
                            width=500, height=500, quality_score=80 - i,
                            status=CandidateStatus.QUEUED.value))


def test_text_screen_skips_image_checks(env):
    # A clean caption must pass text screening even with no image present.
    assert evaluate_text("on god").passed
    # A harmful caption must still be rejected.
    assert not evaluate_text("free crypto airdrop claim now").passed


def test_simulation_alternates_and_captions(env):
    settings_store.update({"caption_mode": "ai", "alternate_meme_animal": True,
                           "posts_per_day": 8, "min_minutes_between_posts": 90,
                           "active_hours_start": 0, "active_hours_end": 24,
                           "max_same_subject_per_day": 8, "max_same_source_per_day": 16})
    _seed()
    res = simulate.simulate(days=1)
    assert res.posts, "should plan posts"
    cats = [p.category for p in res.posts[:4]]
    # memes and animals should alternate at the start
    assert len(set(cats)) == 2, cats
    # at least one animal name caption present (from "This is RexN")
    assert any(p.has_name_caption for p in res.posts)


def test_simulation_empty_pool(env):
    res = simulate.simulate(days=3)
    assert res.count == 0 if hasattr(res, "count") else res.posts == []
    assert res.notes
