"""Learning feedback + taste scoring."""

from __future__ import annotations

from automeme import learning, taste
from automeme.db import session_scope
from automeme.models import Candidate, CandidateStatus


def test_engagement_rate_and_prior(env):
    with session_scope() as s:
        c = Candidate(source="reddit", source_id="1", subject="memes",
                      status=CandidateStatus.POSTED.value, phash="deadbeef",
                      width=500, height=500, metric_impressions=1000,
                      metric_likes=80, metric_reposts=15, metric_bookmarks=5,
                      metric_replies=0)
        s.add(c); s.flush(); cid = c.id

    learning.apply_metrics(cid, impressions=1000, likes=80, reposts=15,
                           bookmarks=5, replies=0)
    with session_scope() as s:
        c = s.get(Candidate, cid)
        assert abs(c.engagement_rate - 0.1) < 1e-6

    prior = learning.source_subject_prior("reddit", "memes")
    assert 0.0 <= prior <= 1.0


def test_taste_score_neutral_without_exemplars(env):
    score = taste.taste_score("abcabcabcabcabca", 500, 500, 0.02)
    assert 0 <= score <= 100


def test_high_engagement_promotes_exemplar(env):
    with session_scope() as s:
        c = Candidate(source="reddit", source_id="2", subject="memes",
                      status=CandidateStatus.POSTED.value, phash="cafecafecafe0000",
                      image_url="http://x/y.png", width=500, height=500)
        s.add(c); s.flush(); cid = c.id

    assert taste.exemplar_count() == 0
    learning.apply_metrics(cid, impressions=1000, likes=200, reposts=50,
                           bookmarks=20, replies=5)
    assert taste.exemplar_count() == 1
