"""Registry of available discovery sources.

New sources are added by writing a class and calling `register()`. The
scheduler asks the registry for enabled sources (respecting the blocklist).
"""

from __future__ import annotations

from .base import Source

SOURCES: dict[str, Source] = {}


def register(source: Source) -> Source:
    SOURCES[source.name] = source
    return source


def get_enabled_sources(blocked_sources: set[str] | None = None) -> list[Source]:
    blocked = blocked_sources or set()
    return [s for name, s in SOURCES.items() if name not in blocked]
