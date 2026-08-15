"""Selection must not deterministically pick the single top-scored item every
time, and RSS (no real upvotes) candidates must score competitively with
real-upvote sources so animals aren't filtered out entirely."""

from __future__ import annotations

from collections import Counter

from automeme import scheduler, scoring, settings_store
from automeme.db import session_scope
from automeme.models import Candidate, CandidateStatus


def test_animal_pseudo_score_competitive_with_meme_score(env):
    # Real-upvote meme (meme-api) vs RSS animal with only a pseudo-score.
    meme_q, _ = scoring.compute_quality(
        velocity=9000, source_score=9000, age_hours=3, phash="a" * 16,
        width=500, height=500, text_density=0.02, source="memeapi",
        subject="memes", has_real_score=True,
    )
    animal_q, _ = scoring.compute_quality(
        velocity=22, source_score=22, age_hours=3, phash="b" * 16,
        width=500, height=500, text_density=0.02, source="reddit_rss",
        subject="aww", has_real_score=False,
    )
    assert animal_q > 50, f"animal score too low to clear typical quality floor: {animal_q}"


def test_pick_is_not_always_the_single_top_score(env):
    settings_store.update({"alternate_meme_animal": False, "pick_score_band": 8.0,
                           "max_same_source_per_day": 999, "max_same_subject_per_day": 999,
                           "queue_ttl_hours": 999})
    scores = [89, 86, 83, 82, 81, 80]
    picks = Counter()
    for trial in range(120):
        with session_scope() as s:
            for i, sc in enumerate(scores):
                s.add(Candidate(source="f", source_id=f"{trial}-{i}", subject="memes",
                                phash=f"{trial:04d}{i:012d}", image_url="http://x",
                                local_path=f"/tmp/{trial}-{i}.png", quality_score=sc,
                                status=CandidateStatus.QUEUED.value))
        picked = scheduler._pick_candidate()
        assert picked is not None
        picks[picked.quality_score] += 1
        with session_scope() as s:
            c = s.get(Candidate, picked.id)
            if c:
                s.delete(c)

    # The top score should NOT win every single time.
    assert picks[89.0] < 120, "top score won literally every trial (no variety)"
    # But it should still win noticeably more often than the others (weighted).
    assert picks[89.0] > picks.get(80.0, 0)
