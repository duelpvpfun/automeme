"""The fail-closed safety pipeline.

Runs every registered check independently and combines the results:

* ANY ``REJECT``               -> overall REJECT
* ANY ``UNCERTAIN`` (and ``reject_on_uncertainty``) -> overall REJECT
* A check raising an exception  -> treated as REJECT (fail closed)
* Only if every check PASSes    -> overall PASS
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field

from .. import settings_store
from .base import CheckResult, SafetyContext, Verdict
from .registry import all_checks, register  # noqa: F401 (re-export register)

# Ensure default checks are registered on import.
from . import checks as _checks  # noqa: E402

_checks.register_defaults()


class SafetyVerdict(str, enum.Enum):
    PASS = "pass"
    REJECT = "reject"


@dataclass
class SafetyDecision:
    verdict: SafetyVerdict
    reasons: list[str] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict == SafetyVerdict.PASS

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reasons": self.reasons,
            "results": self.results,
        }


def registered_checks() -> list[str]:
    return [c.name for c in all_checks()]


# Checks that only inspect the image/local file (skipped when screening a bare
# text string like a caption, which has no image of its own).
_IMAGE_ONLY_CHECKS = {"image_sanity", "ocr_coverage", "nudity_image"}


def evaluate_text(text: str) -> SafetyDecision:
    """Screen a standalone text string (e.g. a generated caption) through the
    text-based checks only. Image checks are skipped since there is no image."""
    ctx = SafetyContext(title=text, caption=text, ocr_text="", ocr_available=True)
    return evaluate(ctx, skip=_IMAGE_ONLY_CHECKS)


def evaluate(ctx: SafetyContext, skip: set[str] | None = None) -> SafetyDecision:
    reject_on_uncertain = bool(settings_store.get("reject_on_uncertainty", True))
    ctx.strictness = str(settings_store.get("safety_strictness", "high"))
    skip = skip or set()

    results: list[CheckResult] = []
    for check in all_checks():
        if getattr(check, "name", "") in skip:
            continue
        try:
            res = check.run(ctx)
        except Exception as exc:  # fail closed
            res = CheckResult(getattr(check, "name", "unknown"),
                              Verdict.REJECT, f"check error (fail-closed): {exc}")
        results.append(res)

    reasons: list[str] = []
    verdict = SafetyVerdict.PASS
    for res in results:
        if res.verdict == Verdict.REJECT:
            verdict = SafetyVerdict.REJECT
            reasons.append(f"{res.check}: {res.reason}")
        elif res.verdict == Verdict.UNCERTAIN and reject_on_uncertain:
            verdict = SafetyVerdict.REJECT
            reasons.append(f"{res.check}: uncertain -> {res.reason}")

    return SafetyDecision(
        verdict=verdict,
        reasons=reasons,
        results=[asdict(r) | {"verdict": r.verdict.value} for r in results],
    )
