"""Dry backtest: show exactly what the bot WOULD post, without posting.

Replays the real posting logic (category alternation, daily caps, per-source /
per-subject diversity, spacing, caption generation + safety screen) over a
simulated window using the candidates currently available, and returns the full
plan: for each simulated slot, the image, the caption text that would be
attached, and the timestamp.

Nothing is written to the database and nothing is sent to X. This is purely a
preview so you can eyeball the account's output before going live.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import captioning, categories, settings_store
from .db import session_scope
from .models import Candidate, CandidateStatus
from .safety import evaluate_text


@dataclass
class SimPost:
    when: datetime
    candidate_id: int
    category: str
    subject: str
    source: str
    image_url: str
    local_path: str
    title: str
    caption: str
    quality_score: float
    has_name_caption: bool


@dataclass
class SimResult:
    days: int
    posts: list[SimPost] = field(default_factory=list)
    total_available: int = 0
    notes: list[str] = field(default_factory=list)


def _caption_for(cand: Candidate) -> str:
    """Mirror scheduler._build_caption: generate + safety-screen, else empty."""
    caption = captioning.generate(cand)
    if not caption:
        return ""
    return caption if evaluate_text(caption).passed else ""


def simulate(days: int = 3) -> SimResult:
    """Produce the would-be posting plan for the last ``days`` days.

    Uses currently-available (queued / awaiting / already-scored) candidates as
    the pool, ranked by quality, and lays them out on the configured schedule.
    """
    st = settings_store.get_all()
    per_day = int(st["posts_per_day"])
    gap_min = int(st["min_minutes_between_posts"])
    start_hour = int(st["active_hours_start"])
    end_hour = int(st["active_hours_end"])
    alternate = bool(st.get("alternate_meme_animal", True))
    max_src = int(st["max_same_source_per_day"])
    max_subj = int(st["max_same_subject_per_day"])

    # Candidate pool: anything that passed safety and is postable.
    postable = {
        CandidateStatus.QUEUED.value,
        CandidateStatus.AWAITING_APPROVAL.value,
        CandidateStatus.POSTED.value,
    }
    with session_scope() as s:
        pool = list(
            s.execute(
                select(Candidate)
                .where(Candidate.status.in_(postable))
                .order_by(Candidate.quality_score.desc())
            ).scalars()
        )
        for c in pool:
            s.expunge(c)

    result = SimResult(days=days, total_available=len(pool))
    if not pool:
        result.notes.append("No candidates available yet. Run discovery first.")
        return result

    # Build the schedule timeline (oldest -> newest) across the window.
    now = datetime.now(timezone.utc)
    slots: list[datetime] = []
    for d in range(days - 1, -1, -1):
        day = (now - timedelta(days=d)).date()
        # place `per_day` slots spaced by gap_min within active hours
        t = datetime(day.year, day.month, day.day, start_hour, 0, tzinfo=timezone.utc)
        for _ in range(per_day):
            if start_hour < end_hour and t.hour >= end_hour:
                break
            if t <= now:
                slots.append(t)
            t = t + timedelta(minutes=max(gap_min, 1))

    used: set[int] = set()
    last_category: str | None = None
    per_day_counts: dict[str, dict[str, int]] = {}

    def counts_for(day_key: str) -> dict[str, int]:
        return per_day_counts.setdefault(day_key, {})

    def pick(preferred: str | None, day_key: str) -> Candidate | None:
        c_src = counts_for(day_key + "|src")
        c_subj = counts_for(day_key + "|subj")
        for want_pref in (True, False):
            for cand in pool:
                if cand.id in used:
                    continue
                if c_src.get(cand.source, 0) >= max_src:
                    continue
                if c_subj.get(cand.subject, 0) >= max_subj:
                    continue
                if want_pref and preferred is not None:
                    if categories.category_for(cand.subject) != preferred:
                        continue
                return cand
            if preferred is None:
                break
        return None

    for when in slots:
        day_key = when.strftime("%Y-%m-%d")
        preferred = None
        if alternate and last_category is not None:
            preferred = categories.ANIMAL if last_category == categories.MEME else categories.MEME

        cand = pick(preferred, day_key)
        if cand is None:
            result.notes.append(f"{when:%Y-%m-%d %H:%M}: ran out of eligible candidates")
            continue

        used.add(cand.id)
        cat = categories.category_for(cand.subject)
        last_category = cat
        counts_for(day_key + "|src")[cand.source] = counts_for(day_key + "|src").get(cand.source, 0) + 1
        counts_for(day_key + "|subj")[cand.subject] = counts_for(day_key + "|subj").get(cand.subject, 0) + 1

        caption = _caption_for(cand)
        name_cap = cat == categories.ANIMAL and bool(caption) and " " not in caption
        result.posts.append(
            SimPost(
                when=when,
                candidate_id=cand.id,
                category=cat,
                subject=cand.subject,
                source=cand.source,
                image_url=cand.image_url,
                local_path=cand.local_path,
                title=cand.title or "",
                caption=caption,
                quality_score=cand.quality_score,
                has_name_caption=name_cap,
            )
        )

    return result


def to_dicts(res: SimResult) -> dict:
    return {
        "days": res.days,
        "total_available": res.total_available,
        "notes": res.notes,
        "count": len(res.posts),
        "posts": [
            {
                "when": p.when.isoformat(),
                "candidate_id": p.candidate_id,
                "category": p.category,
                "subject": p.subject,
                "source": p.source,
                "image_url": p.image_url,
                "title": p.title,
                "caption": p.caption,
                "quality_score": p.quality_score,
                "has_name_caption": p.has_name_caption,
            }
            for p in res.posts
        ],
    }
