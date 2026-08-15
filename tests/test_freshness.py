"""Freshness (age) filtering + Reddit API source gating."""

from __future__ import annotations

import pytest

from automeme import pipeline, settings_store
from automeme.discovery import registry
from automeme.discovery.base import DiscoveredItem
from automeme.discovery.reddit_api import RedditApiSource


class FakeSource:
    name = "fake"

    def __init__(self, items):
        self._items = items

    def fetch(self, limit=60):
        return self._items


@pytest.fixture()
def fake_images(env, png_bytes, monkeypatch):
    from automeme import imaging
    from automeme.config import get_config

    n = {"i": 0}

    def fake_analyze(url):
        n["i"] += 1
        p = get_config().images_path / f"f{n['i']}.png"
        p.write_bytes(png_bytes)
        return imaging.ImageInfo(local_path=str(p), phash=f"{n['i']:016x}",
                                 width=500, height=500, fmt="PNG", ocr_text="",
                                 ocr_available=True, text_density=0.01)

    monkeypatch.setattr(pipeline.imaging, "analyze", fake_analyze)


def _item(sid, age_hours):
    return DiscoveredItem(source="fake", source_id=sid, image_url=f"http://x/{sid}.png",
                          title="fresh meme", author="a", subject="memes",
                          source_score=5000, velocity=1000.0,
                          extra={"age_hours": age_hours})


def test_old_content_rejected(env, fake_images):
    registry.SOURCES.clear()
    registry.register(FakeSource([_item("new", 2.0), _item("old", 100.0)]))
    settings_store.update({"max_content_age_hours": 24, "min_quality_score": 0.0,
                           "min_source_score": 100})
    summary = pipeline.ingest()
    assert summary["accepted"] == 1
    assert summary["too_old"] == 1


def test_reddit_api_disabled_without_credentials(env):
    src = RedditApiSource()
    assert src._enabled() is False
    assert src.fetch() == []
