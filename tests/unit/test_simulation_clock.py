from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    Bar,
    BarSequence,
    DatasetLineage,
    DomainValidationError,
    SimulationClock,
    SimulationClockError,
    Timeframe,
    ValidationStatus,
    canonical_bar_checksum,
)

START = datetime(2026, 9, 27, 12, tzinfo=UTC)


def sequence(count: int = 3) -> BarSequence:
    bars = tuple(
        Bar("US30", Timeframe.M1, START + timedelta(minutes=index),
            Decimal(index + 1), Decimal(index + 2), Decimal(index), Decimal(index + 1))
        for index in range(count)
    )
    lineage = DatasetLineage(
        dataset_revision_id="rev-1", source_dataset_id="seed-1",
        instrument_id="US30", timeframe=Timeframe.M1,
        requested_start=START, requested_end=START + timedelta(minutes=max(count, 1)),
        actual_start=START if bars else None,
        actual_end=START + timedelta(minutes=count) if bars else None,
        bar_count=count, acquired_at=START, validation_status=ValidationStatus.PASS,
        provider_request_json='{"dataset":"seed-1"}', source_checksum="a" * 64,
        canonical_checksum=canonical_bar_checksum(bars),
        checksum_version=BAR_CHECKSUM_VERSION, dataset_format_version="parquet-v1",
    )
    return BarSequence(lineage, bars)


def test_clock_steps_once_and_old_view_never_expands() -> None:
    clock = SimulationClock(sequence())
    assert clock.index == -1 and clock.timestamp is None
    with pytest.raises(SimulationClockError, match="before the first"):
        _ = clock.current
    first = clock.step()
    assert first.index == 0 and first.timestamp == START
    assert not hasattr(first, "_source")
    assert first.history() == (first.current_bar,)
    with pytest.raises(SimulationClockError, match="future"):
        first.bar_at(1)
    with pytest.raises(SimulationClockError, match="future"):
        first.bar_at_time(START + timedelta(minutes=1))
    second = clock.step()
    assert second.index == 1 and len(second.history()) == 2
    assert second.bar_at_time(START) == first.current_bar
    assert len(first.history()) == 1
    with pytest.raises(SimulationClockError, match="future"):
        first.bar_at(1)
    assert clock.step().index == 2
    assert not clock.has_next
    with pytest.raises(SimulationClockError, match="final"):
        clock.step()
    clock.reset()
    assert clock.index == -1 and clock.has_next


def test_clock_warms_up_before_visible_start_and_stops_at_selected_end() -> None:
    clock = SimulationClock(
        sequence(), selected_start=START + timedelta(minutes=1),
        selected_end=START + timedelta(minutes=2),
    )
    warmup = clock.step()
    assert not warmup.is_visible and warmup.visible_count == 1
    visible = clock.step()
    assert visible.is_visible and visible.visible_count == 2
    assert visible.history()[0] == warmup.current_bar
    assert not clock.has_next
    with pytest.raises(SimulationClockError, match="future"):
        visible.bar_at(2)
    with pytest.raises(SimulationClockError, match="no observable bars"):
        SimulationClock(
            sequence(), selected_start=START + timedelta(minutes=5),
            selected_end=START + timedelta(minutes=6),
        )


def test_clock_rejects_empty_and_bad_sequence_order() -> None:
    with pytest.raises(SimulationClockError, match="nonempty"):
        SimulationClock(sequence(0))
    valid = sequence()
    with pytest.raises(DomainValidationError, match="strictly ordered"):
        BarSequence(valid.lineage, (valid.bars[1], valid.bars[0], valid.bars[2]))
    with pytest.raises(DomainValidationError, match="strictly ordered"):
        BarSequence(valid.lineage, (valid.bars[0], valid.bars[0], valid.bars[2]))
    with pytest.raises(SimulationClockError, match="both boundaries"):
        SimulationClock(valid, selected_start=START)
