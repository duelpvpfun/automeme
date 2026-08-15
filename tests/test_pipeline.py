"""End-to-end ingest cycle with a fake discovery source (no network)."""

from __future__ import annotations

import pytest

from automeme import imaging, pipeline, settings_store
from automeme.discovery import registry
from automeme.discovery.base import DiscoveredItem
from automeme.models import Candidate, CandidateStatus


class FakeSource:
    name = "fake"

    def __init__(self, items):
        self._items = items

    def fetch(self, limit: int = 60):
        return self._items


@pytest.fixture()
def fake_images(env, png_bytes, monkeypatch):
    from automeme.config import get_config

    counter = {"n": 0}

    def fake_analyze(url: str):
        counter["n"] += 1
        p = get_config().images_path / f"img{counter['n']}.png"
        p.write_bytes(png_bytes)
        # give each a slightly different phash by varying a pixel isn't trivial
        # here; instead return deterministic info with unique phash per url.
        return imaging.ImageInfo(
            local_path=str(p),
            phash=f"{counter['n']:016x}",
            width=500, height=500, fmt="PNG",
            ocr_text="", ocr_available=True, text_density=0.01,
        )

    monkeypatch.setattr(pipeline.imaging, "analyze", fake_analyze)


def _item(sid, title="funny meme", score=5000, velocity=1000.0):
    return DiscoveredItem(
        source="fake", source_id=sid, image_url=f"http://x/{sid}.png",
        source_url=f"http://x/{sid}", title=title, author="bob",
        subject="memes", source_score=score, velocity=velocity,
        extra={"age_hours": 2.0},
    )


def test_ingest_accepts_clean_and_rejects_bad(env, fake_images, monkeypatch):
    registry.SOURCES.clear()
    registry.register(FakeSource([
        _item("good1", title="when the code finally compiles"),
        _item("bad1", title="free crypto airdrop claim bitcoin now"),
        _item("lowscore", title="meh", score=10),  # below min_source_score
    ]))
    settings_store.set_value("mode", settings_store.MODE_APPROVAL)
    settings_store.set_value("min_quality_score", 0.0)

    summary = pipeline.ingest()
    assert summary["accepted"] == 1
    assert summary["safety_rejected"] == 1
    assert summary["low_source"] == 1

    from automeme.db import session_scope
    from sqlalchemy import select
    with session_scope() as s:
        statuses = [c.status for c in s.execute(select(Candidate)).scalars()]
    assert CandidateStatus.AWAITING_APPROVAL.value in statuses
    assert CandidateStatus.SAFETY_REJECTED.value in statuses


def test_kill_switch_blocks_ingest(env):
    settings_store.set_value("kill_switch", True)
    assert pipeline.ingest() == {"skipped": "kill_switch"}
