"""Near-duplicate detection using perceptual hashes.

Two independent memories are consulted:

1. ``PostedHash`` -- everything ever *posted* (permanent). Prevents re-posting.
2. Other candidates in the DB that are QUEUED/POSTED/AWAITING -- prevents
   queueing two look-alikes at the same time.

A candidate is a duplicate if its pHash is within ``dedup_hamming_threshold``
of any remembered hash.
"""

from __future__ import annotations

from sqlalchemy import select

from . import settings_store
from .db import session_scope
from .imaging import hamming
from .models import Candidate, CandidateStatus, PostedHash

# Statuses whose images are "committed" and should block look-alikes.
_ACTIVE_STATUSES = (
    CandidateStatus.QUEUED.value,
    CandidateStatus.AWAITING_APPROVAL.value,
    CandidateStatus.POSTING.value,
    CandidateStatus.POSTED.value,
)


def find_duplicate(phash: str, exclude_id: int | None = None) -> str | None:
    """Return a human-readable reason if ``phash`` duplicates known content."""
    if not phash:
        return "missing perceptual hash"

    threshold = int(settings_store.get("dedup_hamming_threshold", 6))

    with session_scope() as s:
        for row in s.execute(select(PostedHash)).scalars():
            if hamming(phash, row.phash) <= threshold:
                return f"near-duplicate of already-posted content (candidate {row.candidate_id})"

        stmt = select(Candidate).where(Candidate.status.in_(_ACTIVE_STATUSES))
        for cand in s.execute(stmt).scalars():
            if exclude_id is not None and cand.id == exclude_id:
                continue
            if cand.phash and hamming(phash, cand.phash) <= threshold:
                return f"near-duplicate of active candidate {cand.id} ({cand.status})"

    return None


def remember_posted(phash: str, candidate_id: int | None = None) -> None:
    if not phash:
        return
    with session_scope() as s:
        s.add(PostedHash(phash=phash, candidate_id=candidate_id))
