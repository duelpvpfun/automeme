"""Reddit discovery via public RSS feeds -- FREE, no API key, with timestamps.

Reddit's regular JSON API now 403s datacenter IPs and pushes developers toward
app registration / Devvit. But the plain **RSS feeds** (``/r/<sub>/rising/.rss``)
are still public, keyless, and -- crucially -- include a ``<published>`` time
for every post. That gives us real post AGE so we can prefer fresh, climbing
memes and skip stale ones, without any credentials.

Trade-off vs the JSON API: RSS does not expose the upvote count. We therefore
approximate popularity from the *listing* (``rising`` = gaining traction) and
rank primarily by freshness + listing position. If Reddit API credentials are
configured, ``reddit_api`` supersedes this with true velocity.
"""

from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import httpx

from .. import settings_store
from ..activity import log
from ..config import get_config
from .base import DiscoveredItem
from .memeapi import DEFAULT_SUBREDDITS
from .registry import register

_ATOM = "{http://www.w3.org/2005/Atom}"
_IMG_RE = re.compile(r'https://(?:i|preview)\.redd\.it/[^\s"&]+\.(?:jpg|jpeg|png|webp)', re.I)
_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


def _first_image(content_html: str) -> str:
    if not content_html:
        return ""
    text = html.unescape(content_html)
    m = _IMG_RE.search(text)
    return m.group(0).split("?")[0] if m else ""


class RedditRssSource:
    name = "reddit_rss"

    # CORE hubs are checked EVERY cycle: this is where a "generational" meme
    # actually breaks, so we never miss it waiting for a rotation. Big, high-
    # traffic meme + animal subs.
    _CORE_SUBREDDITS = (
        "memes", "dankmemes", "me_irl", "funny", "wholesomememes",  # memes
        "aww", "rarepuppers", "Eyebleach",                          # animals
    )

    # The long tail rotates a few per cycle for variety (niche subs where
    # missing the very first minutes matters far less).
    _ROTATING_SUBREDDITS = (
        "MemeEconomy", "AdviceAnimals", "facepalm", "clevercomebacks", "meirl",
        "comedyheaven", "terriblefacebookmemes", "bonehurtingjuice", "okbuddyretard",
        "cats", "AnimalsBeingDerps", "dogpictures", "shibe", "shibainu",
        "IllegallySmolCats", "babyelephantgifs", "AnimalsBeingBros", "goldenretrievers",
    )

    _RSS_SUBREDDITS = _CORE_SUBREDDITS + _ROTATING_SUBREDDITS

    _cursor = 0
    _listing_cursor = 0
    _ROTATE_PER_CYCLE = 4  # long-tail subs added on top of the core each cycle

    # Rotate the listing each cycle so the pool keeps changing: today's top,
    # this week's top, and what's hot right now -- a deep, refreshing well.
    _LISTINGS = ("top?t=day", "hot", "top?t=week", "rising")

    def __init__(self, subreddits: tuple[str, ...] | None = None,
                 listings: tuple[str, ...] | None = None):
        # If a custom list is given (tests), use it verbatim as the core.
        if subreddits is not None:
            self.core = tuple(subreddits)
            self.rotating: tuple[str, ...] = ()
        else:
            self.core = self._CORE_SUBREDDITS
            self.rotating = self._ROTATING_SUBREDDITS
        self.subreddits = self.core + self.rotating
        self.listings = listings  # None => rotate through _LISTINGS

    def _listing(self) -> str:
        cls = type(self)
        lst = self._LISTINGS[cls._listing_cursor % len(self._LISTINGS)]
        cls._listing_cursor += 1
        return lst

    def _batch(self) -> tuple[str, ...]:
        """Core hubs EVERY cycle (never miss a breakout) + a rotating slice of
        the long tail for variety."""
        if not self.rotating:
            return self.core
        cls = type(self)
        n = self._ROTATE_PER_CYCLE
        start = cls._cursor % len(self.rotating)
        picked = [self.rotating[(start + i) % len(self.rotating)] for i in range(n)]
        cls._cursor = (start + n) % len(self.rotating)
        return self.core + tuple(picked)

    def _get_feed_bytes(self, client: httpx.Client, url: str) -> bytes | None:
        """Fetch a feed with a couple of polite retries. Reddit rate-limits
        datacenter IPs and sometimes returns an empty body; back off and retry."""
        for attempt in range(3):
            resp = client.get(url)
            if resp.status_code == 200 and resp.content:
                return resp.content
            time.sleep(1.5 * (attempt + 1))
        return None

    def _fetch_feed(self, client: httpx.Client, sub: str, listing: str,
                    max_age_h: float) -> list[DiscoveredItem]:
        # listing may carry a query, e.g. "top?t=day"; split path vs params.
        path, _, extra = listing.partition("?")
        url = f"https://www.reddit.com/r/{sub}/{path}/.rss?limit=25"
        if extra:
            url += "&" + extra
        content = self._get_feed_bytes(client, url)
        if not content:
            return []
        root = ET.fromstring(content)
        now = datetime.now(timezone.utc)
        items: list[DiscoveredItem] = []
        for pos, entry in enumerate(root.findall(f"{_ATOM}entry")):
            def txt(tag: str) -> str:
                el = entry.find(f"{_ATOM}{tag}")
                return (el.text or "") if el is not None else ""

            title = txt("title")
            content_el = entry.find(f"{_ATOM}content")
            content = content_el.text if content_el is not None else ""
            img = _first_image(content or "")
            if not img:
                continue

            published = txt("published") or txt("updated")
            try:
                created = datetime.fromisoformat(published)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except ValueError:
                created = now
            age_h = max((now - created).total_seconds() / 3600.0, 0.05)
            if age_h > max_age_h:
                continue  # too old -> skip (freshness gate)

            link_el = entry.find(f"{_ATOM}link")
            permalink = link_el.get("href") if link_el is not None else ""
            post_id = txt("id").rsplit("/", 1)[-1] or permalink
            author_el = entry.find(f"{_ATOM}author/{_ATOM}name")
            author = (author_el.text or "").replace("/u/", "") if author_el is not None else ""

            # No score in RSS: approximate. Newer + higher in the rising list =
            # stronger. velocity ~ position-decayed recency.
            pseudo_score = max(1, 25 - pos)
            velocity = pseudo_score / age_h

            items.append(
                DiscoveredItem(
                    source=self.name,
                    source_id=str(post_id),
                    image_url=img,
                    source_url=permalink,
                    title=title,
                    author=author,
                    subject=sub.lower(),
                    source_score=pseudo_score,
                    velocity=velocity,
                    # no_real_score: RSS has no upvote count, so exempt it from
                    # the min_source_score floor (it's ranked by freshness).
                    extra={"age_hours": age_h, "listing": listing,
                           "rss": True, "no_real_score": True},
                )
            )
        return items

    def fetch(self, limit: int = 50) -> list[DiscoveredItem]:
        # If real Reddit API creds exist, defer to the richer reddit_api source.
        if get_config().has_reddit_credentials:
            return []
        max_age_h = float(settings_store.get("max_content_age_hours", 24))
        cfg = get_config()
        headers = {"User-Agent": cfg.user_agent or "Mozilla/5.0 (automeme)"}
        # One rotating listing per cycle (unless explicitly overridden), so the
        # pool keeps changing across cycles instead of returning the same set.
        listings = self.listings if self.listings else (self._listing(),)
        # Weekly-top can be older than the daily freshness cap; give it headroom
        # so it can add variety (still proven-viral content).
        out: dict[str, DiscoveredItem] = {}
        try:
            with httpx.Client(headers=headers, timeout=15.0, follow_redirects=True) as client:
                for sub in self._batch():
                    for listing in listings:
                        eff_age = max_age_h * (7 if "week" in listing else 1)
                        try:
                            for item in self._fetch_feed(client, sub, listing, eff_age):
                                prev = out.get(item.source_id)
                                if prev is None or item.velocity > prev.velocity:
                                    out[item.source_id] = item
                        except (httpx.HTTPError, ET.ParseError):
                            continue
                        time.sleep(1.0)  # be polite: avoid Reddit rate-limiting
        except httpx.HTTPError as exc:
            log("reddit_rss_error", str(exc), level="warning")
            return []
        log("reddit_rss_ok",
            f"fresh items={len(out)} listing={listings[0]}")
        return list(out.values())


register(RedditRssSource())
