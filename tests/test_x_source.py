"""X discovery source: must never spend credit unless explicitly enabled."""

from __future__ import annotations

from automeme import settings_store
from automeme.discovery.x_source import XSource


def test_disabled_by_default_spends_nothing(env):
    s = XSource()
    ok, reason = s._enabled()
    assert ok is False
    assert s.fetch() == []


def test_enabled_but_no_credentials_still_safe(env):
    settings_store.set_value("x_discovery_enabled", True)
    s = XSource()
    ok, reason = s._enabled()
    assert ok is False
    assert reason == "no X read credentials"
    assert s.fetch() == []  # no network call, no credit


def test_calls_api_only_when_enabled_and_credentialed(env, monkeypatch):
    settings_store.set_value("x_discovery_enabled", True)

    # Pretend credentials exist.
    from automeme.config import get_config
    cfg = get_config()
    monkeypatch.setattr(cfg, "x_bearer_token", "fake-bearer", raising=False)

    called = {"n": 0}

    class FakeResp:
        data = None
        includes = {}

    class FakeClient:
        def search_recent_tweets(self, **kw):
            called["n"] += 1
            assert kw["max_results"] <= 100  # credit cap enforced
            return FakeResp()

    s = XSource()
    monkeypatch.setattr(s, "_ensure_client", lambda: FakeClient())
    s.fetch()
    assert called["n"] == 1  # exactly one search request per cycle
