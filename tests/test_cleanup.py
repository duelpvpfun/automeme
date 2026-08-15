"""Image disk cleanup: posted/rejected images are removed, queued are kept."""

from __future__ import annotations

from pathlib import Path

from automeme import scheduler
from automeme.config import get_config
from automeme.db import session_scope
from automeme.models import Candidate, CandidateStatus


def _make_image(name: str) -> str:
    p = get_config().images_path / name
    p.write_bytes(b"fake-image-bytes")
    return str(p)


def test_cleanup_removes_posted_keeps_queued(env):
    posted_img = _make_image("posted.png")
    queued_img = _make_image("queued.png")
    orphan = _make_image("orphan.png")  # no candidate references this

    with session_scope() as s:
        s.add(Candidate(source="f", source_id="1", phash="a", image_url="http://x",
                        local_path=posted_img, status=CandidateStatus.POSTED.value))
        s.add(Candidate(source="f", source_id="2", phash="b", image_url="http://x",
                        local_path=queued_img, status=CandidateStatus.QUEUED.value))

    scheduler.cleanup_images()

    assert not Path(posted_img).exists(), "posted image should be deleted"
    assert Path(queued_img).exists(), "queued image should be kept"
    assert not Path(orphan).exists(), "orphan file should be pruned"
