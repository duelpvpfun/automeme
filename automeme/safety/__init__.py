"""Multi-layer, fail-closed safety system.

Design principles (per requirements):

* Several **independent** checks. Each returns a verdict on its own concern.
* **Fail closed**: if any check REJECTs, the content is rejected. If any check
  is UNCERTAIN and ``reject_on_uncertainty`` is on (default), it is rejected.
  If a check raises an unexpected error, that is treated as a rejection too.
* Order does not matter for the outcome -- every check runs so the report is
  complete, and the content passes only if *all* checks pass.
"""

from .pipeline import (
    SafetyDecision,
    SafetyVerdict,
    evaluate,
    evaluate_text,
    registered_checks,
)

__all__ = [
    "SafetyDecision",
    "SafetyVerdict",
    "evaluate",
    "evaluate_text",
    "registered_checks",
]
