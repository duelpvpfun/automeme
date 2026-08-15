"""Discovery via meme-api.com (D3vd/Meme_Api).

A free, no-auth service that proxies trending Reddit memes as JSON, so it works
from environments where Reddit's own endpoints block datacenter IPs. Returns
title, subreddit, author, upvotes and a direct image URL.

We query a spread of meme subreddits and pull a batch from each. Because the
API does not expose post age, velocity is approximated from the upvote count
(popular-now proxy); the scorer still favors higher-signal items.
"""

from __future__ import annotations

import httpx

from ..config import get_config
from .base import DiscoveredItem
from .registry import register

DEFAULT_SUBREDDITS: tuple[str, ...] = (
    # memes
    "memes",
    "dankmemes",
    "me_irl",
    "wholesomememes",
    "funny",
    "MemeEconomy",
    "AdviceAnimals",
    # cute animals (viral)
    "aww",
    "rarepuppers",
    "cats",
    "AnimalsBeingDerps",
    "Eyebleach",
    "dogpictures",
    "shibe",
    "shibainu",
)

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _is_image_url(url: str) -> bool:
    u = url.lower().split("?")[0]
    return u.endswith(_IMAGE_EXT)


class MemeApiSource:
    name = "memeapi"

    def __init__(self, subreddits: tuple[str, ...] | None = None,
                 base_url: str = "https://meme-api.com"):
        self.subreddits = tuple(subreddits or DEFAULT_SUBREDDITS)
        self.base_url = base_url.rstrip("/")

    def _fetch_sub(self, client: httpx.Client, sub: str, count: int) -> list[DiscoveredItem]:
        resp = client.get(f"{self.base_url}/gimme/{sub}/{count}")
        resp.raise_for_status()
        data = resp.json()
        out: list[DiscoveredItem] = []
        for m in data.get("memes", []) or ([data] if "url" in data else []):
            if m.get("nsfw") or m.get("spoiler"):
                continue
            img = m.get("url", "")
            if not _is_image_url(img):
                continue
            ups = int(m.get("ups", 0) or 0)
            post_link = m.get("postLink", "")
            source_id = post_link.rsplit("/", 1)[-1] if post_link else img
            out.append(
                DiscoveredItem(
                    source=self.name,
                    source_id=source_id,
                    image_url=img,
                    source_url=post_link,
                    title=m.get("title", "") or "",
                    author=m.get("author", "") or "",
                    subject=str(m.get("subreddit", sub)).lower(),
                    source_score=ups,
                    source_comments=0,
                    # No age from this API: approximate velocity from popularity.
                    velocity=float(ups),
                    extra={"age_hours": 6.0},
                )
            )
        return out

    def fetch(self, limit: int = 50) -> list[DiscoveredItem]:
        cfg = get_config()
        headers = {"User-Agent": cfg.user_agent}
        per = max(1, min(limit // max(len(self.subreddits), 1), 50))
        out: dict[str, DiscoveredItem] = {}
        with httpx.Client(headers=headers, timeout=20.0, follow_redirects=True) as client:
            for sub in self.subreddits:
                try:
                    for item in self._fetch_sub(client, sub, per):
                        prev = out.get(item.source_id)
                        if prev is None or item.source_score > prev.source_score:
                            out[item.source_id] = item
                except (httpx.HTTPError, ValueError):
                    continue
        return list(out.values())


register(MemeApiSource())
