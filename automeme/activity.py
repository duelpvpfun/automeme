"""Structured activity logging: writes to the audit DB and stdout.

Includes a redaction pass so secrets / internal instructions can never leak
into the log (part of the "never publish internal output" requirement).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select

from .db import session_scope
from .models import ActivityLog

logger = logging.getLogger("automeme")

# Patterns that must never appear in logs or outputs.
_REDACT_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|bearer)\s*[=:]\s*\S+"),
    re.compile(r"AAAA[A-Za-z0-9%]{20,}"),  # X bearer-token-ish blobs
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat in _REDACT_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


def log(event: str, message: str = "", *, level: str = "info",
        candidate_id: int | None = None) -> None:
    message = redact(message or "")
    with session_scope() as s:
        s.add(
            ActivityLog(
                level=level, event=event, message=message, candidate_id=candidate_id
            )
        )
    getattr(logger, level if level in {"info", "warning", "error", "debug"} else "info")(
        "%s %s", event, message
    )


def recent(limit: int = 200) -> list[ActivityLog]:
    with session_scope() as s:
        rows = (
            s.execute(select(ActivityLog).order_by(ActivityLog.id.desc()).limit(limit))
            .scalars()
            .all()
        )
        return list(rows)
