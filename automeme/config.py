"""Static configuration loaded from environment / .env.

These are *boot-time* settings (paths, credentials, secrets). Behavioral
settings that the control panel can change at runtime (posting frequency,
allowed subjects, safety thresholds, mode) live in the database instead --
see automeme.settings_store.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOMEME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Security
    panel_password: str = Field(default="change-me")
    secret_key: str = Field(default="insecure-dev-key-change-me")

    # Runtime
    dry_run: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: str = "./data"

    # X credentials (empty = cannot publish)
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_secret: str = ""
    x_bearer_token: str = ""

    # X discovery (reads cost credit on pay-per-use, so it's off by default and
    # only ever runs when explicitly enabled AND read credentials exist).
    x_discovery_enabled: bool = False

    # Reddit API (free "script" app). Gives real post AGE -> true velocity, so we
    # catch memes while they're climbing instead of after they've peaked.
    # Create at https://www.reddit.com/prefs/apps (type: script).
    reddit_client_id: str = ""
    reddit_client_secret: str = ""

    # Startup bootstrap (useful on hosts like Railway where the DB starts empty).
    # If set, these override the stored mode/paused ONCE at boot so the bot can
    # go live purely from environment variables. Leave unset to use the panel.
    start_mode: str = ""          # "auto" | "approval" | "" (leave as-is)
    start_unpaused: bool = False  # true => unpause on boot

    @property
    def has_reddit_credentials(self) -> bool:
        return bool(self.reddit_client_id and self.reddit_client_secret)

    # Optional LLM for caption_mode="ai". Leave key blank to use the built-in,
    # no-cost caption generator instead. Any OpenAI-compatible endpoint works.
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    user_agent: str = "automeme/1.0 (autonomous meme curator)"

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def images_path(self) -> Path:
        p = self.data_path / "images"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> Path:
        return self.data_path / "automeme.db"

    @property
    def has_x_write_credentials(self) -> bool:
        return all(
            [
                self.x_api_key,
                self.x_api_secret,
                self.x_access_token,
                self.x_access_secret,
            ]
        )

    @property
    def has_x_read_credentials(self) -> bool:
        return bool(self.x_bearer_token) or self.has_x_write_credentials


@lru_cache
def get_config() -> Config:
    return Config()
