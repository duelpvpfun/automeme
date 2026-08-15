"""Imaging + perceptual dedup."""

from __future__ import annotations

from automeme import dedup, imaging


def test_hamming():
    assert imaging.hamming("ffff", "ffff") == 0
    assert imaging.hamming("0000", "ffff") == 16
    assert imaging.hamming("", "ffff") == 999


def test_analyze_and_dedup(env, png_bytes, monkeypatch):
    # Avoid network: stub download to write our in-memory PNG to disk.
    from automeme.config import get_config

    def fake_download(url: str):
        p = get_config().images_path / "test.png"
        p.write_bytes(png_bytes)
        return p

    monkeypatch.setattr(imaging, "download", fake_download)

    info = imaging.analyze("http://example.com/x.png")
    assert info.phash
    assert info.width == 400 and info.height == 400

    # Not a duplicate yet.
    assert dedup.find_duplicate(info.phash) is None

    # Remember as posted -> now it's a duplicate.
    dedup.remember_posted(info.phash, candidate_id=1)
    assert dedup.find_duplicate(info.phash) is not None
