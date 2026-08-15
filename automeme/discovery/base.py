"""Base types for pluggable discovery sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class DiscoveredItem:
    """Normalized representation of one piece of discovered content."""

    source: str
    source_id: str
    image_url: str
    source_url: str = ""
    title: str = ""
    author: str = ""
    subject: str = ""            # e.g. subreddit / topic bucket
    source_score: int = 0        # upvotes / likes at source
    source_comments: int = 0
    velocity: float = 0.0        # growth rate estimate (score per hour)
    extra: dict = field(default_factory=dict)


class Source(Protocol):
    """A discovery source. Implementations must be side-effect free (read only)."""

    name: str

    def fetch(self, limit: int = 50) -> list[DiscoveredItem]:
        ...
