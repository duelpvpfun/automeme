"""Shared types for safety checks."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Protocol


class Verdict(str, enum.Enum):
    PASS = "pass"
    UNCERTAIN = "uncertain"
    REJECT = "reject"


@dataclass
class CheckResult:
    check: str
    verdict: Verdict
    reason: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class SafetyContext:
    """Everything a check may need about a candidate."""

    title: str = ""
    caption: str = ""
    ocr_text: str = ""
    ocr_available: bool = False
    author: str = ""
    subject: str = ""
    source: str = ""
    source_url: str = ""
    image_url: str = ""
    local_path: str = ""
    width: int = 0
    height: int = 0
    text_density: float = 0.0
    strictness: str = "high"

    @property
    def all_text(self) -> str:
        return " \n ".join(
            t for t in (self.title, self.caption, self.ocr_text) if t
        )


class Check(Protocol):
    """A single independent safety check."""

    name: str

    def run(self, ctx: SafetyContext) -> CheckResult:
        ...
