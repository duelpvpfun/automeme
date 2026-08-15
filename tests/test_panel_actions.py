"""Panel diagnostic, reset, and live-log actions."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from automeme.control.app import create_app
from automeme.db import session_scope
from automeme.models import Candidate, CandidateStatus


@pytest.fixture()
def client(env):
    app = create_app(start_scheduler=False)
    with TestClient(app) as c:
        c.post("/login", data={"password": "test-password"}, follow_redirects=False)
        yield c


def test_diagnose(client):
    r = client.get("/api/diagnose").json()
    assert "can_post_now" in r and "queued" in r and "hint" in r


def test_logs_stream(client):
    r = client.get("/api/logs?after_id=0").json()
    assert "last_id" in r and "lines" in r


def test_reset_keeps_posted(client):
    with session_scope() as s:
        s.add(Candidate(source="f", source_id="q", phash="a", image_url="http://x",
                        status=CandidateStatus.QUEUED.value))
        s.add(Candidate(source="f", source_id="p", phash="b", image_url="http://x",
                        status=CandidateStatus.POSTED.value))
    r = client.post("/api/actions/reset", json={"keep_posted": True}).json()
    assert r["removed"] == 1  # only the queued one
    with session_scope() as s:
        from sqlalchemy import func, select
        posted = s.execute(
            select(func.count()).select_from(Candidate).where(
                Candidate.status == CandidateStatus.POSTED.value)
        ).scalar_one()
        assert posted == 1  # posted history preserved
