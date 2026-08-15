"""Discovery from X (Twitter) via the v2 recent-search API.

IMPORTANT COST NOTE
-------------------
X is pay-per-use: every search request spends account credit. This source is
therefore **disabled unless BOTH** are true:

* ``AUTOMEME_X_DISCOVERY_ENABLED=true``, and
* read credentials exist (bearer token or OAuth user tokens).

When gated off, ``fetch`` returns ``[]`` immediately and spends nothing.

Strategy for frugality:
* one combined query per cycle (not one per keyword),
* request only popular image tweets from the last window,
* a hard cap on results (``max_results``) so a cycle can't run away with credit.

Ranking: velocity = engagement / age_hours, where engagement is
likes + reposts + replies + quotes, mirroring the Reddit scorer so both sources
feed the same pipeline consistently.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import settings_store
from ..activity import log
from ..config import get_config
from .base import DiscoveredItem
from .registry import register

try:
    import tweepy  # type: ignore

    _TWEEPY_OK = True
except Exception:  # pragma: no cover
    _TWEEPY_OK = False

# Only surface genuinely meme-like, safe-by-default image tweets. The safety
# pipeline still screens everything afterwards; this just keeps the query cheap
# and on-topic. ``-is:retweet`` avoids duplicates, ``has:images`` requires an
# image, ``-is:reply`` avoids reply chains, ``lang:en`` keeps text screenable.
DEFAULT_QUERY = (
    "(meme OR memes OR funny OR relatable OR shitpost) "
    "has:images -is:retweet -is:reply -is:quote lang:en -is:nullcast"
)


class XSource:
    name = "x"

    def __init__(self, query: str | None = None):
        self.query = query or DEFAULT_QUERY
        self._client = None

    def _ensure_client(self):
        cfg = get_config()
        if not _TWEEPY_OK:
            return None
        if self._client is None:
            self._client = tweepy.Client(
                bearer_token=cfg.x_bearer_token or None,
                consumer_key=cfg.x_api_key or None,
                consumer_secret=cfg.x_api_secret or None,
                access_token=cfg.x_access_token or None,
                access_token_secret=cfg.x_access_secret or None,
            )
        return self._client

    def _enabled(self) -> tuple[bool, str]:
        cfg = get_config()
        if not cfg.x_discovery_enabled and not settings_store.get("x_discovery_enabled", False):
            return False, "x_discovery disabled"
        if not cfg.has_x_read_credentials:
            return False, "no X read credentials"
        if not _TWEEPY_OK:
            return False, "tweepy not installed"
        return True, "ok"

    def fetch(self, limit: int = 50) -> list[DiscoveredItem]:
        ok, reason = self._enabled()
        if not ok:
            # No credit spent. Silent unless explicitly enabled but misconfigured.
            if reason == "no X read credentials":
                log("x_discovery_skipped", reason, level="warning")
            return []

        client = self._ensure_client()
        if client is None:
            return []

        # Hard cap to protect credit; X allows 10..100 per request.
        max_results = int(settings_store.get("x_discovery_max_results", 25))
        max_results = max(10, min(max_results, 100))

        try:
            resp = client.search_recent_tweets(
                query=self.query,
                max_results=max_results,
                tweet_fields=["public_metrics", "created_at", "possibly_sensitive", "lang"],
                media_fields=["url", "type", "width", "height"],
                expansions=["attachments.media_keys", "author_id"],
                user_fields=["username"],
            )
        except Exception as exc:  # noqa: BLE001
            log("x_discovery_error", f"{exc}", level="warning")
            return []

        if resp is None or not resp.data:
            return []

        media = {m.media_key: m for m in (resp.includes or {}).get("media", [])}
        users = {u.id: u for u in (resp.includes or {}).get("users", [])}
        now = datetime.now(timezone.utc)
        out: list[DiscoveredItem] = []

        for tw in resp.data:
            if getattr(tw, "possibly_sensitive", False):
                continue
            keys = (tw.attachments or {}).get("media_keys", []) if tw.attachments else []
            img_url = ""
            w = h = 0
            for k in keys:
                m = media.get(k)
                if m and m.type == "photo" and getattr(m, "url", ""):
                    img_url = m.url
                    w = getattr(m, "width", 0) or 0
                    h = getattr(m, "height", 0) or 0
                    break
            if not img_url:
                continue

            pm = tw.public_metrics or {}
            engagement = (
                int(pm.get("like_count", 0))
                + int(pm.get("retweet_count", 0))
                + int(pm.get("reply_count", 0))
                + int(pm.get("quote_count", 0))
            )
            created = tw.created_at or now
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_h = max((now - created).total_seconds() / 3600.0, 0.1)
            author = users.get(tw.author_id)
            username = author.username if author else ""

            out.append(
                DiscoveredItem(
                    source=self.name,
                    source_id=str(tw.id),
                    image_url=img_url,
                    source_url=f"https://x.com/{username}/status/{tw.id}" if username else f"https://x.com/i/status/{tw.id}",
                    title=tw.text or "",
                    author=username,
                    subject="x",
                    source_score=int(pm.get("like_count", 0)),
                    source_comments=int(pm.get("reply_count", 0)),
                    velocity=engagement / age_h,
                    extra={"age_hours": age_h, "width": w, "height": h},
                )
            )
        log("x_discovery_ok", f"tweets={len(out)} spent~1 search request")
        return out


register(XSource())
