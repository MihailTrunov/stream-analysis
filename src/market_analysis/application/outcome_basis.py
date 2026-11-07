"""Detection-time direction and exact price arithmetic shared by v1 metrics."""

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import cast

from market_analysis.application.outcome_evaluation import OutcomeInput

_DIRECTION_FIELDS = {
    ("EMA_STRUCTURE_REVERSAL_V1", "1"): "candidate_direction",
    ("EMA_RECLAIM_STRUCTURE_CONTINUATION_V1", "1"): "source_direction",
}


def captured_direction(source: OutcomeInput) -> str | None:
    detector = source.context.get("detector")
    if not isinstance(detector, Mapping) or detector.get("context_status") != "AVAILABLE":
        return None
    pattern_id, version = detector.get("pattern_id"), detector.get("pattern_version")
    if not isinstance(pattern_id, str) or not isinstance(version, str):
        return None
    field = _DIRECTION_FIELDS.get((pattern_id, version))
    context = detector.get("context")
    if field is None or not isinstance(context, Mapping):
        return None
    direction = context.get(field)
    return "UP" if direction == "UP" else "DOWN" if direction == "DOWN" else None


def exact_difference(left: Decimal, right: Decimal) -> Decimal:
    exponent = min(cast(int, left.as_tuple().exponent), cast(int, right.as_tuple().exponent))
    precision = max(28, max(left.adjusted(), right.adjusted()) - exponent + 2)
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        return left - right
