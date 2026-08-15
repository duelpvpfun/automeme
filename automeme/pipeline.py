"""Content pipeline orchestration.

``ingest`` runs one discovery cycle:

    discover -> (blocklist filter) -> download+analyze -> safety -> dedup ->
    score -> persist as QUEUED (auto mode) or AWAITING_APPROVAL (approval mode)

Every stage is defensive: a single bad item is logged and skipped, never
crashing the cycle. Nothing here posts to X -- that is the scheduler's job.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from . import dedup, imaging, scoring, settings_store
from .activity import log
from .db import session_scope
from .discovery.base import DiscoveredItem
from .discovery.registry import get_enabled_sources
from .models import Blocklist, Candidate, CandidateStatus
from .safety import SafetyVerdict, evaluate
from .safety.base import SafetyContext


def _load_blocklist() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {"source": set(), "topic": set(), "author": set(), "phrase": set()}
    with session_scope() as s:
        for row in s.execute(select(Blocklist)).scalars():
            out.setdefault(row.kind, set()).add(row.value.lower())
    return out


def _is_blocked(item: DiscoveredItem, bl: dict[str, set[str]]) -> str | None:
    if item.subject.lower() in bl["topic"]:
        return f"subject '{item.subject}' is blocklisted"
    if item.author.lower() in bl["author"]:
        return "author is blocklisted"
    text = f"{item.title}".lower()
    for phrase in bl["phrase"]:
        if phrase and phrase in text:
            return f"blocklisted phrase: {phrase}"
    return None


def _subject_allowed(subject: str) -> bool:
    allowed = settings_store.get("allowed_subjects", []) or []
    if not allowed:
        return True
    return subject.lower() in {a.lower() for a in allowed}


def _already_seen(source: str, source_id: str) -> bool:
    with session_scope() as s:
        stmt = select(Candidate.id).where(
            Candidate.source == source, Candidate.source_id == source_id
        )
        return s.execute(stmt).first() is not None


def _persist(item: DiscoveredItem, info: imaging.ImageInfo, quality: float,
             breakdown: dict, safety_dict: dict, status: str) -> int:
    with session_scope() as s:
        c = Candidate(
            source=item.source,
            source_id=item.source_id,
            source_url=item.source_url,
            image_url=item.image_url,
            title=item.title,
            author=item.author,
            subject=item.subject,
            local_path=info.local_path,
            phash=info.phash,
            width=info.width,
            height=info.height,
            source_score=item.source_score,
            source_comments=item.source_comments,
            velocity=item.velocity,
            ocr_text=info.ocr_text,
            quality_score=quality,
            safety_passed=True,
            safety_report=json.dumps({"safety": safety_dict, "scoring": breakdown}),
            status=status,
        )
        s.add(c)
        s.flush()
        return c.id


def ingest(limit_per_source: int = 60) -> dict:
    """Run one full discovery+screening cycle. Returns a summary dict."""
    if settings_store.get("kill_switch", False):
        log("ingest_skipped", "kill switch active")
        return {"skipped": "kill_switch"}

    bl = _load_blocklist()
    sources = get_enabled_sources(blocked_sources=bl["source"])
    min_source_score = int(settings_store.get("min_source_score", 500))
    min_quality = float(settings_store.get("min_quality_score", 55.0))
    max_age_h = float(settings_store.get("max_content_age_hours", 24))
    mode = settings_store.get("mode", settings_store.MODE_APPROVAL)
    queued_status = (
        CandidateStatus.AWAITING_APPROVAL.value
        if mode == settings_store.MODE_APPROVAL
        else CandidateStatus.QUEUED.value
    )

    summary = {
        "discovered": 0, "blocked": 0, "seen": 0, "low_source": 0,
        "too_old": 0, "image_failed": 0, "safety_rejected": 0, "duplicate": 0,
        "low_quality": 0, "accepted": 0,
    }

    for source in sources:
        try:
            items = source.fetch(limit=limit_per_source)
        except Exception as exc:  # noqa: BLE001
            log("source_error", f"{source.name}: {exc}", level="warning")
            continue

        for item in items:
            summary["discovered"] += 1

            reason = _is_blocked(item, bl)
            if reason:
                summary["blocked"] += 1
                continue
            if not _subject_allowed(item.subject):
                summary["blocked"] += 1
                continue
            if item.source_score < min_source_score:
                summary["low_source"] += 1
                continue
            # Freshness gate: skip anything older than the cutoff so we only post
            # NEW memes early, not old ones that already spread to X.
            age_h = float(item.extra.get("age_hours", 0.0))
            if age_h and age_h > max_age_h:
                summary["too_old"] += 1
                continue
            if _already_seen(item.source, item.source_id):
                summary["seen"] += 1
                continue

            try:
                info = imaging.analyze(item.image_url)
            except imaging.ImageError:
                summary["image_failed"] += 1
                continue

            ctx = SafetyContext(
                title=item.title,
                caption="",
                ocr_text=info.ocr_text,
                ocr_available=info.ocr_available,
                author=item.author,
                subject=item.subject,
                source=item.source,
                source_url=item.source_url,
                image_url=item.image_url,
                local_path=info.local_path,
                width=info.width,
                height=info.height,
                text_density=info.text_density,
            )
            decision = evaluate(ctx)
            if decision.verdict == SafetyVerdict.REJECT:
                summary["safety_rejected"] += 1
                _record_rejection(item, info, decision.to_dict())
                continue

            dup = dedup.find_duplicate(info.phash)
            if dup:
                summary["duplicate"] += 1
                continue

            age_hours = float(item.extra.get("age_hours", 1.0))
            quality, breakdown = scoring.compute_quality(
                velocity=item.velocity,
                source_score=item.source_score,
                age_hours=age_hours,
                phash=info.phash,
                width=info.width,
                height=info.height,
                text_density=info.text_density,
                source=item.source,
                subject=item.subject,
            )
            if quality < min_quality:
                summary["low_quality"] += 1
                continue

            cid = _persist(item, info, quality, breakdown, decision.to_dict(),
                           queued_status)
            summary["accepted"] += 1
            log("candidate_accepted",
                f"id={cid} q={quality} src={item.source}/{item.subject} status={queued_status}",
                candidate_id=cid)

    log("ingest_complete", json.dumps(summary))
    return summary


def _record_rejection(item: DiscoveredItem, info: imaging.ImageInfo,
                      safety_dict: dict) -> None:
    with session_scope() as s:
        if s.execute(
            select(Candidate.id).where(
                Candidate.source == item.source, Candidate.source_id == item.source_id
            )
        ).first():
            return
        c = Candidate(
            source=item.source,
            source_id=item.source_id,
            source_url=item.source_url,
            image_url=item.image_url,
            title=item.title,
            author=item.author,
            subject=item.subject,
            local_path=info.local_path,
            phash=info.phash,
            width=info.width,
            height=info.height,
            source_score=item.source_score,
            velocity=item.velocity,
            ocr_text=info.ocr_text,
            safety_passed=False,
            safety_report=json.dumps({"safety": safety_dict}),
            status=CandidateStatus.SAFETY_REJECTED.value,
            status_reason="; ".join(safety_dict.get("reasons", []))[:500],
        )
        s.add(c)
