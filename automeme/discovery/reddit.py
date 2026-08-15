"""Reddit discovery via the public JSON endpoints (no OAuth required).

We pull from a curated set of meme / humor subreddits using the ``rising`` and
``hot`` listings, which surface content that is *gaining traction fast* -- the
key signal for finding memes before they are overused.

Velocity = score / age_in_hours, which approximates growth rate and lets the
scorer prefer fast-climbing posts over already-saturated ones.
"""

from __future__ import annotations

import time
from typing import Iterable

import httpx

from ..config import get_config
from .base import DiscoveredItem
from .registry import register

# Curated seed subreddits. Users can blacklist any of these from the panel,
# and can add topics via allowed_subjects filtering downstream.
DEFAULT_SUBREDDITS: tuple[str, ...] = (
    "memes",
    "dankmemes",
    "MemeEconomy",
    "funny",
    "me_irl",
    "wholesomememes",
    "AdviceAnimals",
    "comics",
    "CrappyDesign",
    "aww",
    "AnimalsBeingDerps",
    "nextfuckinglevel",
    "oddlysatisfying",
    "mildlyinteresting",
)

_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp")


def _is_image_url(url: str) -> bool:
    u = url.lower().split("?")[0]
    return u.endswith(_IMAGE_EXT)


class RedditSource:
    name = "reddit"

    def __init__(self, subreddits: Iterable[str] | None = None,
                 listings: tuple[str, ...] = ("rising", "hot")):
        self.subreddits = tuple(subreddits or DEFAULT_SUBREDDITS)
        self.listings = listings

    def _fetch_listing(self, client: httpx.Client, sub: str, listing: str,
                       limit: int) -> list[DiscoveredItem]:
        url = f"https://www.reddit.com/r/{sub}/{listing}.json"
        params = {"limit": str(limit), "raw_json": "1"}
        if listing == "top":
            params["t"] = "day"
        resp = client.get(url, params=params)
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
                # try preview
                try:
                    img = (
                        post["preview"]["images"][0]["source"]["url"]
                    )
                except (KeyError, IndexError, TypeError):
                    continue
                if not _is_image_url(img):
                    continue
            score = int(post.get("score", 0) or 0)
            created = float(post.get("created_utc", now) or now)
            age_h = max((now - created) / 3600.0, 0.1)
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
                    velocity=score / age_h,
                    extra={"age_hours": age_h, "listing": listing},
                )
            )
        return items

    def fetch(self, limit: int = 50) -> list[DiscoveredItem]:
        cfg = get_config()
        headers = {"User-Agent": cfg.user_agent}
        out: dict[str, DiscoveredItem] = {}
        per = max(5, limit // max(len(self.subreddits), 1))
        with httpx.Client(headers=headers, timeout=15.0, follow_redirects=True) as client:
            for sub in self.subreddits:
                for listing in self.listings:
                    try:
                        for item in self._fetch_listing(client, sub, listing, per):
                            # keep the highest-velocity version if seen twice
                            prev = out.get(item.source_id)
                            if prev is None or item.velocity > prev.velocity:
                                out[item.source_id] = item
                    except (httpx.HTTPError, ValueError):
                        # A single subreddit/listing failing must not abort discovery.
                        continue
        return list(out.values())


register(RedditSource())
