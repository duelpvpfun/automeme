"""SQLAlchemy ORM models."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CandidateStatus(str, enum.Enum):
    DISCOVERED = "discovered"      # freshly pulled from a source
    SCORED = "scored"              # passed scoring
    SAFETY_PASSED = "safety_passed"
    SAFETY_REJECTED = "safety_rejected"
    DUPLICATE = "duplicate"
    QUEUED = "queued"             # approved / awaiting scheduled post
    AWAITING_APPROVAL = "awaiting_approval"
    POSTING = "posting"
    POSTED = "posted"
    FAILED = "failed"
    REJECTED = "rejected"         # manually rejected
    DISABLED = "disabled"         # manually disabled/removed from queue


class Candidate(Base):
    """A single discovered piece of content moving through the pipeline."""

    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("source", "source_id", name="uq_source_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Provenance
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(255), default="")
    subject: Mapped[str] = mapped_column(String(128), default="", index=True)

    # Local storage
    local_path: Mapped[str] = mapped_column(Text, default="")
    phash: Mapped[str] = mapped_column(String(64), default="", index=True)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)

    # Discovery metrics (raw, from source)
    source_score: Mapped[int] = mapped_column(Integer, default=0)
    source_comments: Mapped[int] = mapped_column(Integer, default=0)
    velocity: Mapped[float] = mapped_column(Float, default=0.0)  # growth rate

    # Extracted / analyzed
    ocr_text: Mapped[str] = mapped_column(Text, default="")

    # Scoring
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    # Safety
    safety_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    safety_report: Mapped[str] = mapped_column(Text, default="")  # JSON

    # Lifecycle
    status: Mapped[str] = mapped_column(
        String(32), default=CandidateStatus.DISCOVERED.value, index=True
    )
    status_reason: Mapped[str] = mapped_column(Text, default="")

    # Publishing
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    x_post_id: Mapped[str] = mapped_column(String(64), default="")
    caption: Mapped[str] = mapped_column(Text, default="")

    # Engagement (learned)
    metric_impressions: Mapped[int] = mapped_column(Integer, default=0)
    metric_likes: Mapped[int] = mapped_column(Integer, default=0)
    metric_reposts: Mapped[int] = mapped_column(Integer, default=0)
    metric_bookmarks: Mapped[int] = mapped_column(Integer, default=0)
    metric_replies: Mapped[int] = mapped_column(Integer, default=0)
    engagement_rate: Mapped[float] = mapped_column(Float, default=0.0)
    metrics_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class ActivityLog(Base):
    """Append-only audit trail of everything the system does."""

    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    event: Mapped[str] = mapped_column(String(64), index=True)
    candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    """Runtime settings editable from the control panel (key/value JSON)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")  # JSON encoded


class Blocklist(Base):
    """Blacklisted sources, subreddits, topics, authors, or phrases."""

    __tablename__ = "blocklist"
    __table_args__ = (UniqueConstraint("kind", "value", name="uq_block"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # source|topic|author|phrase
    value: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PostedHash(Base):
    """Perceptual hashes of everything ever posted (permanent dedup memory).

    ``phash`` is UNIQUE: the database itself physically refuses to record the
    same posted image twice, so a duplicate post can never be committed.
    """

    __tablename__ = "posted_hashes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phash: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    candidate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TasteExemplar(Base):
    """Reference posts that define the account's desired taste (e.g. @s8n).

    Used to nudge scoring toward a recognizable style: image-only, minimal
    caption, clean single-panel memes. Exemplars carry a perceptual hash and
    coarse visual traits so new candidates can be scored by similarity.
    """

    __tablename__ = "taste_exemplars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(255), default="")   # e.g. "s8n"
    image_url: Mapped[str] = mapped_column(Text, default="")
    phash: Mapped[str] = mapped_column(String(64), default="", index=True)
    aspect_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    text_density: Mapped[float] = mapped_column(Float, default=0.0)  # 0-1 OCR coverage
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DailyStat(Base):
    """Per-day counters used to enforce posting limits and diversity."""

    __tablename__ = "daily_stats"
    __table_args__ = (UniqueConstraint("day", "kind", "value", name="uq_daily"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD (UTC)
    kind: Mapped[str] = mapped_column(String(32))  # total|source|subject
    value: Mapped[str] = mapped_column(String(128), default="")
    count: Mapped[int] = mapped_column(Integer, default=0)
