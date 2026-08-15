"""Authenticated Reddit discovery (OAuth "script" app) with REAL post age.

Why this exists: meme-api.com gives upvotes but not post age, so we can't tell a
meme that's *climbing right now* from one that peaked days ago. The official
Reddit API returns ``created_utc``, which lets us compute true velocity
(upvotes / hours) AND hard-filter anything older than ``max_content_age_hours``.

That age filter is the whole point: it keeps us posting FRESH memes early --
before they spread to X -- instead of reposting last week's viral leftovers.

Free to use: create a "script" app at https://www.reddit.com/prefs/apps and set
AUTOMEME_REDDIT_CLIENT_ID / AUTOMEME_REDDIT_CLIENT_SECRET. Falls back silently
(returns []) if credentials are absent, so meme-api still covers discovery.
"""

from __future__ import annotations

import time

import httpx

from .. import settings_store
from ..activity import log
from ..config import get_config
from .base import DiscoveredItem
from .memeapi import DEFAULT_SUBREDDITS
from .registry import register

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")  # gifs excluded: often video-y
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API = "https://oauth.reddit.com"


def _is_image_url(url: str) -> bool:
    u = (url or "").lower().split("?")[0]
    return u.endswith(_IMAGE_EXT)


class RedditApiSource:
    name = "reddit_api"

    def __init__(self, subreddits: tuple[str, ...] | None = None,
                 listings: tuple[str, ...] = ("rising", "hot")):
        self.subreddits = tuple(subreddits or DEFAULT_SUBREDDITS)
        self.listings = listings
        self._token: str | None = None
        self._token_exp = 0.0

    def _enabled(self) -> bool:
        return get_config().has_reddit_credentials

    def _get_token(self, client: httpx.Client) -> str | None:
        now = time.time()
        if self._token and now < self._token_exp - 30:
            return self._token
        cfg = get_config()
        resp = client.post(
            _TOKEN_URL,
            auth=(cfg.reddit_client_id, cfg.reddit_client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": cfg.user_agent},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data.get("access_token")
        self._token_exp = now + float(data.get("expires_in", 3600))
        return self._token

    def _fetch_listing(self, client: httpx.Client, token: str, sub: str,
                       listing: str, limit: int, max_age_h: float) -> list[DiscoveredItem]:
        cfg = get_config()
        resp = client.get(
            f"{_API}/r/{sub}/{listing}",
            params={"limit": str(limit), "raw_json": "1"},
            headers={"Authorization": f"Bearer {token}", "User-Agent": cfg.user_agent},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        items: list[DiscoveredItem] = []
        now = time.time()
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            if post.get("over_18") or post.get("stickied") or post.get("is_video"):
                continue
            img = post.get("url_overridden_by_dest") or post.get("url", "")
            if not _is_image_url(img):
                try:
                    img = post["preview"]["images"][0]["source"]["url"]
                except (KeyError, IndexError, TypeError):
                    continue
                if not _is_image_url(img):
                    continue
            created = float(post.get("created_utc", now) or now)
            age_h = max((now - created) / 3600.0, 0.05)
            if age_h > max_age_h:
                continue  # too old -> likely already spread; skip
            score = int(post.get("score", 0) or 0)
            items.append(
                DiscoveredItem(
                    source=self.name,
                    source_id=str(post.get("id", "")),
                    image_url=img,
                    source_url="https://www.reddit.com" + post.get("permalink", ""),
                    title=post.get("title", "") or "",
                    author=post.get("author", "") or "",
                    subject=sub.lower(),
                    source_score=score,
                    source_comments=int(post.get("num_comments", 0) or 0),
                    velocity=score / age_h,   # TRUE growth rate
                    extra={"age_hours": age_h, "listing": listing},
                )
            )
        return items

    def fetch(self, limit: int = 50) -> list[DiscoveredItem]:
        if not self._enabled():
            return []
        max_age_h = float(settings_store.get("max_content_age_hours", 24))
        cfg = get_config()
        out: dict[str, DiscoveredItem] = {}
        per = max(10, limit // max(len(self.subreddits), 1))
        try:
            with httpx.Client(follow_redirects=True) as client:
                token = self._get_token(client)
                if not token:
                    log("reddit_api_auth_failed", "no token", level="warning")
                    return []
                for sub in self.subreddits:
                    for listing in self.listings:
                        try:
                            for item in self._fetch_listing(
                                client, token, sub, listing, per, max_age_h
                            ):
                                prev = out.get(item.source_id)
                                if prev is None or item.velocity > prev.velocity:
                                    out[item.source_id] = item
                        except httpx.HTTPError:
                            continue
        except httpx.HTTPError as exc:
            log("reddit_api_error", str(exc), level="warning")
            return []
        log("reddit_api_ok", f"fresh items={len(out)} (max_age={max_age_h}h)")
        return list(out.values())


register(RedditApiSource())
