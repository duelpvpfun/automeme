"""Registry of safety checks."""

from __future__ import annotations

from .base import Check

_CHECKS: dict[str, Check] = {}


def register(check: Check) -> Check:
    _CHECKS[check.name] = check
    return check


def all_checks() -> list[Check]:
    return list(_CHECKS.values())


def clear() -> None:  # for tests
    _CHECKS.clear()
