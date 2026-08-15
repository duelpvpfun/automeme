"""X (Twitter) client wrapper.

Uses tweepy for OAuth 1.0a media upload + tweet creation (v2). Reading post
metrics uses the v2 API with the bearer token when available.

Two important safety behaviors:

* **Dry-run**: when ``AUTOMEME_DRY_RUN=true`` (default) nothing is ever sent to
  X. ``post_image`` records what it *would* have posted and returns a synthetic
  id. This makes the whole system fully testable without credentials.
* **No credentials**: if write credentials are missing, publishing raises
  ``PublishError`` (the scheduler treats that as a soft failure and pauses),
  never silently doing nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from ..activity import log
from ..config import get_config

try:
    import tweepy  # type: ignore

    _TWEEPY_OK = True
except Exception:  # pragma: no cover
    _TWEEPY_OK = False


class PublishError(Exception):
    """A failure that happened BEFORE anything was sent to X -- safe to retry."""


class PublishUncertainError(PublishError):
    """The tweet request may have succeeded on X's side even though we could
    not confirm it (e.g. response timeout). NEVER retry/release dedup for this;
    treat the image as posted (better to skip a meme than double-post it)."""


@dataclass
class PublishResult:
    post_id: str
    dry_run: bool


@dataclass
class PostMetrics:
    impressions: int = 0
    likes: int = 0
    reposts: int = 0
    bookmarks: int = 0
    replies: int = 0


class XClient:
    def __init__(self) -> None:
        self.cfg = get_config()
        self._api_v1 = None   # media upload
        self._client_v2 = None

    # -- lazy auth -----------------------------------------------------------
    def _ensure_write(self) -> None:
        if self.cfg.dry_run:
            return
        if not self.cfg.has_x_write_credentials:
            raise PublishError("X write credentials are not configured")
        if not _TWEEPY_OK:
            raise PublishError("tweepy is not installed")
        if self._client_v2 is None:
            auth = tweepy.OAuth1UserHandler(
                self.cfg.x_api_key,
                self.cfg.x_api_secret,
                self.cfg.x_access_token,
                self.cfg.x_access_secret,
            )
            self._api_v1 = tweepy.API(auth)
            self._client_v2 = tweepy.Client(
                consumer_key=self.cfg.x_api_key,
                consumer_secret=self.cfg.x_api_secret,
                access_token=self.cfg.x_access_token,
                access_token_secret=self.cfg.x_access_secret,
                bearer_token=self.cfg.x_bearer_token or None,
            )

    # -- publish -------------------------------------------------------------
    def post_image(self, local_path: str, caption: str = "") -> PublishResult:
        if self.cfg.dry_run:
            fake = f"dryrun-{uuid.uuid4().hex[:16]}"
            log("post_dryrun", f"would post image={local_path} caption={caption!r}")
            return PublishResult(post_id=fake, dry_run=True)

        self._ensure_write()
        try:
            media = self._api_v1.media_upload(filename=local_path)  # type: ignore
        except Exception as exc:  # noqa: BLE001
            # Nothing was sent to X yet -> safe to retry this image later.
            raise PublishError(f"failed to upload media: {exc}") from exc

        media_id = getattr(media, "media_id_string", None) or str(media.media_id)
        try:
            resp = self._client_v2.create_tweet(  # type: ignore
                text=caption or None,
                media_ids=[media_id],
            )
            post_id = str(resp.data["id"])
        except Exception as exc:  # noqa: BLE001
            # UNSAFE to assume this failed on X's side too (could be a response
            # timeout after the tweet was actually created). Raise a distinct
            # error so the caller does NOT release the dedup reservation --
            # better to skip this image than risk posting it again for real.
            raise PublishUncertainError(
                f"tweet may have been created but confirmation failed: {exc}"
            ) from exc
        return PublishResult(post_id=post_id, dry_run=False)

    def delete_post(self, post_id: str) -> bool:
        if self.cfg.dry_run or post_id.startswith("dryrun-"):
            log("delete_dryrun", f"would delete post={post_id}")
            return True
        self._ensure_write()
        try:
            self._client_v2.delete_tweet(post_id)  # type: ignore
            return True
        except Exception as exc:  # noqa: BLE001
            log("delete_failed", f"post={post_id}: {exc}", level="warning")
            return False

    # -- metrics -------------------------------------------------------------
    def fetch_metrics(self, post_id: str) -> PostMetrics | None:
        if self.cfg.dry_run or post_id.startswith("dryrun-"):
            return None
        if not self.cfg.has_x_read_credentials or not _TWEEPY_OK:
            return None
        try:
            if self._client_v2 is None:
                self._client_v2 = tweepy.Client(bearer_token=self.cfg.x_bearer_token or None)
            resp = self._client_v2.get_tweet(
                post_id,
                tweet_fields=["public_metrics", "non_public_metrics"],
            )
            pm = (resp.data.get("public_metrics") or {}) if resp.data else {}
            npm = (resp.data.get("non_public_metrics") or {}) if resp.data else {}
            return PostMetrics(
                impressions=int(npm.get("impression_count", 0)),
                likes=int(pm.get("like_count", 0)),
                reposts=int(pm.get("retweet_count", 0)),
                bookmarks=int(pm.get("bookmark_count", 0)),
                replies=int(pm.get("reply_count", 0)),
            )
        except Exception as exc:  # noqa: BLE001
            log("metrics_fetch_failed", f"post={post_id}: {exc}", level="warning")
            return None


_client: XClient | None = None


def get_client() -> XClient:
    global _client
    if _client is None:
        _client = XClient()
    return _client


def reset_client_for_tests() -> None:
    global _client
    _client = None
