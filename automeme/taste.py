"""Taste profile: learn a recognizable style from reference accounts (e.g. @s8n).

The account @s8n posts image-only, caption-less, clean single-panel memes that
reliably go viral. We can't reliably scrape X without credentials, so taste is
learned from **reference exemplars** the operator supplies (image URLs of posts
they like) plus the account's own best-performing posts over time.

For each exemplar we store a perceptual hash and a couple of coarse visual
traits (aspect ratio, text density). A candidate's ``taste_score`` (0-100) is a
blend of:

* visual similarity to the nearest exemplars (via pHash distance), and
* how well its traits match the exemplar distribution (aspect ratio, low text).

If there are no exemplars yet, taste_score is a neutral 50 so the system still
works out of the box.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from . import imaging
from .activity import log
from .db import session_scope
from .imaging import hamming
from .models import TasteExemplar

# A gentle default style prior matching the s8n aesthetic even before the
# operator adds exemplars: near-square to portrait, low baked-in text.
_STYLE_PRIOR_ASPECT = (0.6, 1.4)


@dataclass
class TasteProfile:
    count: int
    avg_aspect: float
    avg_text_density: float


def add_exemplar(image_url: str, label: str = "s8n", weight: float = 1.0) -> TasteExemplar | None:
    """Download + analyze a reference image and store it as a taste exemplar."""
    try:
        info = imaging.analyze(image_url)
    except imaging.ImageError as exc:
        log("taste_exemplar_failed", f"{image_url}: {exc}", level="warning")
        return None
    aspect = info.width / info.height if info.height else 0.0
    with session_scope() as s:
        ex = TasteExemplar(
            label=label,
            image_url=image_url,
            phash=info.phash,
            aspect_ratio=aspect,
            text_density=info.text_density,
            weight=weight,
        )
        s.add(ex)
        s.flush()
        s.refresh(ex)
        s.expunge(ex)
    log("taste_exemplar_added", f"label={label} url={image_url}")
    return ex


def _load_exemplars() -> list[TasteExemplar]:
    with session_scope() as s:
        rows = list(s.execute(select(TasteExemplar)).scalars())
        for r in rows:
            s.expunge(r)
        return rows


def profile() -> TasteProfile:
    rows = _load_exemplars()
    if not rows:
        return TasteProfile(0, 1.0, 0.05)
    avg_aspect = sum(r.aspect_ratio for r in rows) / len(rows)
    avg_text = sum(r.text_density for r in rows) / len(rows)
    return TasteProfile(len(rows), avg_aspect, avg_text)


def taste_score(phash: str, width: int, height: int, text_density: float) -> float:
    """Return 0-100 similarity to the learned taste (higher = more on-brand)."""
    aspect = width / height if height else 0.0
    rows = _load_exemplars()

    # Trait component: reward square-ish, low-text images (s8n-like).
    trait = 50.0
    if _STYLE_PRIOR_ASPECT[0] <= aspect <= _STYLE_PRIOR_ASPECT[1]:
        trait += 15
    trait += max(0.0, 15.0 * (1.0 - min(text_density * 5, 1.0)))  # less text -> higher

    if not rows or not phash:
        return max(0.0, min(trait, 100.0))

    # Similarity component: nearest exemplar by pHash (0 dist = identical).
    best = min(hamming(phash, r.phash) for r in rows if r.phash) if any(r.phash for r in rows) else 64
    # Map hamming distance (0..~32 meaningful) to 0..100 similarity.
    sim = max(0.0, 100.0 * (1.0 - best / 32.0))

    # Blend: 60% similarity to real exemplars, 40% trait prior.
    return max(0.0, min(0.6 * sim + 0.4 * trait, 100.0))


def exemplar_count() -> int:
    with session_scope() as s:
        return len(list(s.execute(select(TasteExemplar)).scalars()))
