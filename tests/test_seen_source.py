"""Permanent seen-source memory: a posted item's Candidate row can be deleted,
but discovery must never treat the same source post as new again."""

from __future__ import annotations

from automeme import pipeline, settings_store
from automeme.db import session_scope
from automeme.discovery.base import DiscoveredItem
from automeme.discovery.registry import SOURCES
from automeme.models import Candidate


class FakeSource:
    name = "fake"

    def __init__(self, item: DiscoveredItem):
        self._item = item

    def fetch(self, limit: int = 60):
        return [self._item]


def _item():
    return DiscoveredItem(
        source="fake", source_id="THE_BLACK_MEME", image_url="http://x/black.png",
        title="black meme", subject="memes", source_score=9999, velocity=999,
        extra={"age_hours": 1.0},
    )


def test_deleted_candidate_never_reappears(env, png_bytes, monkeypatch):
    from automeme import imaging
    from automeme.config import get_config

    def fake_analyze(url):
        p = get_config().images_path / "black.png"
        p.write_bytes(png_bytes)
        return imaging.ImageInfo(local_path=str(p), phash="1111222233334444",
                                 width=500, height=500, fmt="PNG", ocr_text="",
                                 ocr_available=True, text_density=0.0)

    monkeypatch.setattr(pipeline.imaging, "analyze", fake_analyze)
    settings_store.update({"min_source_score": 0, "min_quality_score": 0})

    SOURCES.clear()
    SOURCES["fake"] = FakeSource(_item())

    r1 = pipeline.ingest()
    assert r1["accepted"] == 1

    # Simulate what happens after a successful post: the Candidate row is
    # deleted (to keep the queue lean), exactly like scheduler.post_one() does.
    with session_scope() as s:
        from sqlalchemy import select
        for c in s.execute(select(Candidate)).scalars():
            s.delete(c)

    # The exact same source post is "discovered" again next cycle.
    r2 = pipeline.ingest()
    assert r2["accepted"] == 0
    assert r2["seen"] == 1, "must be blocked as already-seen, not re-accepted"
