"""Independent literal projection of the v2 reference identity contract."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import (
    MarketEvent,
    MarketEventType,
    MarketStateFrame,
    market_event_semantic_ref,
)
from market_analysis.indicators.swing_structure import StructureLineage
from market_analysis.indicators.trend_leg import EmaCrossTransition, TrendDirection


def test_reference_v2_keeps_semantic_lineage_and_excludes_execution_uuid():
    at = datetime(2026, 1, 5, 10, tzinfo=UTC)
    lineage = StructureLineage(
        "execution-one", "source", "US30", Timeframe.M1, "calendar", "c" * 64
    )
    evidence = EmaCrossTransition(
        "cross-definition",
        TrendDirection.UP,
        at,
        at,
        1,
        Decimal("9"),
        Decimal("10"),
        Decimal("11"),
        Decimal("10"),
        None,
        None,
        lineage,
    )
    event = MarketEvent(MarketEventType.EMA_CROSS, at, at, 0, "trend_leg", evidence)
    frame = MarketStateFrame(
        Bar("US30", Timeframe.M1, at, 10, 12, 9, 11),
        1,
        "execution-one",
        "source",
        "c" * 64,
        None,
        {},
        {},
        {},
        (event,),
    )
    timestamp = "2026-01-05T10:00:00Z"
    expected = {
        "dataset_revision_id": "source",
        "detection_config_hash": "c" * 64,
        "instrument_id": "US30",
        "timeframe": "1m",
        "event_type": "EMA_CROSS",
        "event_time": timestamp,
        "detection_time": timestamp,
        "ordinal": 0,
        "source_instance_id": "trend_leg",
        "evidence": {
            "definition_id": "cross-definition",
            "direction": "UP",
            "event_time": timestamp,
            "detection_time": timestamp,
            "bar_index": 1,
            "previous_close": "9",
            "previous_ema": "10",
            "current_close": "11",
            "current_ema": "10",
            "ended_segment": None,
            "started_segment": None,
            "lineage": {
                "dataset_revision_id": "source",
                "instrument_id": "US30",
                "timeframe": "1m",
                "calendar_id": "calendar",
                "detection_config_hash": "c" * 64,
            },
        },
    }
    canonical = json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = sha256(f"market-event-ref-v2\n{canonical}".encode()).hexdigest()
    assert market_event_semantic_ref(frame, event) == digest
    for changed_lineage, equal in [
        (replace(lineage, run_id="execution-two"), True),
        (replace(lineage, calendar_id="other-calendar"), False),
        (replace(lineage, detection_config_hash="d" * 64), False),
    ]:
        changed = replace(event, evidence=replace(evidence, lineage=changed_lineage))
        other = replace(frame, run_id="execution-two", market_events_this_bar=(changed,))
        assert (market_event_semantic_ref(other, changed) == digest) is equal
    # Operational lineage remains available in the audit/debug view.
    assert "execution-one" in frame.debug_json()
