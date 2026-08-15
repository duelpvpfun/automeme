"""Learning from engagement.

Two feedback loops:

1. **Source/subject priors** -- for each (source, subject) we track the average
   engagement rate of posts we've made. Better-performing buckets get a higher
   prior, which feeds back into scoring so the curator gravitates toward what
   works for *this* account.

2. **Taste reinforcement** -- top-performing posts are promoted into the taste
   exemplar set so the style profile keeps sharpening toward what goes viral.

Engagement rate is computed conservatively as
``(likes + reposts + bookmarks + replies) / max(impressions, 1)``.
"""

from __future__ import annotations

from sqlalchemy import select

from .activity import log
from .db import session_scope
from .models import Candidate, CandidateStatus, TasteExemplar


def compute_engagement_rate(c: Candidate) -> float:
    interactions = (
        c.metric_likes + c.metric_reposts + c.metric_bookmarks + c.metric_replies
    )
    denom = max(c.metric_impressions, 1)
    return round(interactions / denom, 5)


def source_subject_prior(source: str, subject: str) -> float:
    """Return a 0..1 prior for how well this bucket performs for us.

    Neutral 0.5 when we have no history yet, so it neither helps nor hurts.
    """
    with session_scope() as s:
        rows = list(
            s.execute(
                select(Candidate).where(
                    Candidate.status == CandidateStatus.POSTED.value,
                    Candidate.metric_impressions > 0,
                )
            ).scalars()
        )
    if not rows:
        return 0.5

    def rate(c: Candidate) -> float:
        return c.engagement_rate or compute_engagement_rate(c)

    global_avg = sum(rate(c) for c in rows) / len(rows)
    bucket = [c for c in rows if c.source == source and c.subject == subject]
    if not bucket or global_avg <= 0:
        return 0.5
    bucket_avg = sum(rate(c) for c in bucket) / len(bucket)
    # Ratio vs global, squashed into 0..1 around 0.5.
    ratio = bucket_avg / global_avg
    return max(0.0, min(0.5 * ratio, 1.0))


def apply_metrics(
    candidate_id: int,
    *,
    impressions: int,
    likes: int,
    reposts: int,
    bookmarks: int,
    replies: int,
) -> None:
    with session_scope() as s:
        c = s.get(Candidate, candidate_id)
        if c is None:
            return
        c.metric_impressions = impressions
        c.metric_likes = likes
        c.metric_reposts = reposts
        c.metric_bookmarks = bookmarks
        c.metric_replies = replies
        c.engagement_rate = compute_engagement_rate(c)
    log("metrics_updated", f"candidate={candidate_id} er={c.engagement_rate}",
        candidate_id=candidate_id)
    _maybe_reinforce_taste(candidate_id)


def _maybe_reinforce_taste(candidate_id: int, min_er: float = 0.05) -> None:
    """Promote a strongly performing post into the taste exemplar set."""
    with session_scope() as s:
        c = s.get(Candidate, candidate_id)
        if c is None or not c.phash:
            return
        if c.engagement_rate < min_er:
            return
        exists = s.execute(
            select(TasteExemplar).where(TasteExemplar.phash == c.phash)
        ).scalar_one_or_none()
        if exists:
            return
        aspect = c.width / c.height if c.height else 0.0
        s.add(
            TasteExemplar(
                label="self-viral",
                image_url=c.image_url,
                phash=c.phash,
                aspect_ratio=aspect,
                text_density=0.0,
                weight=min(1.0 + c.engagement_rate * 5, 3.0),
            )
        )
    log("taste_reinforced", f"candidate={candidate_id} promoted to exemplar",
        candidate_id=candidate_id)
