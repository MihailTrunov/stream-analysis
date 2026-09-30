"""Map validated detector intents into the durable PatternInstance boundary."""

from __future__ import annotations

from market_analysis.detection.records import TransitionIntent, json_value
from market_analysis.persistence.pattern_instances import LifecycleStep


def lifecycle_step_from_intent(intent: TransitionIntent) -> LifecycleStep:
    """Preserve runtime transition evidence using its canonical JSON representation."""
    if not isinstance(intent, TransitionIntent):
        raise TypeError("intent must be a TransitionIntent")
    rationale = json_value(intent.rationale)
    assert isinstance(rationale, dict)
    return LifecycleStep(
        intent.from_state,
        intent.to_state,
        intent.trigger_id,
        intent.event_time,
        intent.detection_time,
        rationale=rationale,
    )
