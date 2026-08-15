"""Reddit RSS source: offline parsing of image + timestamp (no network)."""

from __future__ import annotations

import html

from automeme.discovery.reddit_rss import RedditRssSource, _first_image


def test_first_image_extracts_redd_it():
    content = html.escape(
        '<a href="x"><img src="https://i.redd.it/abc123.jpeg?width=640&s=xx" /></a>'
    )
    assert _first_image(content) == "https://i.redd.it/abc123.jpeg"


def test_first_image_none_when_absent():
    assert _first_image("<p>just text, no image</p>") == ""
    assert _first_image("") == ""


def test_defers_to_api_when_credentials_present(env, monkeypatch):
    from automeme.config import get_config
    cfg = get_config()
    monkeypatch.setattr(cfg, "reddit_client_id", "id", raising=False)
    monkeypatch.setattr(cfg, "reddit_client_secret", "secret", raising=False)
    # With API creds present, RSS source yields nothing (reddit_api takes over).
    assert RedditRssSource().fetch() == []
