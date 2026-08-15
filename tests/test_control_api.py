"""Control panel: auth enforcement + core API workflows."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from automeme import settings_store
from automeme.control.app import create_app
from automeme.control import service
from automeme.db import session_scope
from automeme.models import Candidate, CandidateStatus


@pytest.fixture()
def client(env):
    app = create_app(start_scheduler=False)
    with TestClient(app) as c:
        yield c


def _login(client) -> None:
    r = client.post("/login", data={"password": "test-password"}, follow_redirects=False)
    assert r.status_code == 303


def test_requires_auth(client):
    assert client.get("/api/stats").status_code == 401


def test_bad_password_rejected(client):
    r = client.post("/login", data={"password": "wrong"}, follow_redirects=False)
    assert r.status_code == 401


def test_login_and_stats(client):
    _login(client)
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert "mode" in r.json()


def test_approve_and_disable_flow(client):
    _login(client)
    with session_scope() as s:
        c = Candidate(source="f", source_id="1", image_url="http://x/a.png",
                      local_path="/tmp/a.png", phash="ab", subject="memes",
                      quality_score=80, status=CandidateStatus.AWAITING_APPROVAL.value)
        s.add(c); s.flush(); cid = c.id

    assert client.post(f"/api/candidates/{cid}/approve").json()["ok"] is True
    with session_scope() as s:
        assert s.get(Candidate, cid).status == CandidateStatus.QUEUED.value

    assert client.post(f"/api/candidates/{cid}/disable").json()["ok"] is True
    with session_scope() as s:
        assert s.get(Candidate, cid).status == CandidateStatus.DISABLED.value


def test_settings_bounds_enforced(client):
    _login(client)
    r = client.post("/api/settings", json={"max_posts_per_day": 999})
    assert r.json()["max_posts_per_day"] == 24  # clamped


def test_emergency_controls(client):
    _login(client)
    r = client.post("/api/control/kill", json={"active": True})
    assert r.json()["kill_switch"] is True
    assert r.json()["paused"] is True


def test_blocklist_crud(client):
    _login(client)
    assert client.post("/api/blocklist", json={"kind": "topic", "value": "politics"}).json()["ok"]
    rows = client.get("/api/blocklist").json()
    assert any(r["value"] == "politics" for r in rows)
    bid = rows[0]["id"]
    assert client.delete(f"/api/blocklist/{bid}").json()["ok"]


def test_purge_queue(client):
    _login(client)
    with session_scope() as s:
        for i in range(3):
            s.add(Candidate(source="f", source_id=str(i), image_url="http://x",
                            phash=f"h{i}", status=CandidateStatus.QUEUED.value))
    assert client.post("/api/queue/purge").json()["purged"] == 3
