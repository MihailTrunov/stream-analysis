from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    AnalyticalPipeline,
    Bar,
    BarSequence,
    DatasetLineage,
    ObservableBars,
    ReplayCursor,
    ReplayCursorError,
    SimulationClock,
    SimulationClockError,
    Timeframe,
    ValidationStatus,
    canonical_bar_checksum,
)

START = datetime(2026, 9, 27, 12, tzinfo=UTC)


class RecordingPipeline:
    """Stateful fake pipeline recording views, events, and its own resets."""

    def __init__(self) -> None:
        self.views: list[ObservableBars] = []
        self.resets = 0
        self.events: list[str] = []

    def process_bar(self, view: ObservableBars) -> None:
        self.views.append(view)
        self.events.append(f"bar:{view.index}")

    def reset(self) -> None:
        self.resets += 1
        self.views = []
        self.events.append("reset")


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


def cursor(count: int = 3) -> tuple[ReplayCursor, RecordingPipeline]:
    pipeline = RecordingPipeline()
    return ReplayCursor(SimulationClock(sequence(count)), pipeline), pipeline


def observed(pipeline: RecordingPipeline) -> tuple[list[int], list[Bar], list[str]]:
    return (
        [view.index for view in pipeline.views],
        [view.current_bar for view in pipeline.views],
        list(pipeline.events),
    )


def test_step_one_processes_each_bar_once_in_canonical_order() -> None:
    bars = sequence(3).bars
    replay, pipeline = cursor(3)
    assert replay.index == -1 and replay.timestamp is None and replay.has_next
    first = replay.step_one()
    assert first.index == 0 and first.timestamp == START
    assert first.current_bar == bars[0]
    second = replay.step_one()
    third = replay.step_one()
    assert [view.index for view in (first, second, third)] == [0, 1, 2]
    indexes, processed, events = observed(pipeline)
    assert indexes == [0, 1, 2] and processed == list(bars)
    assert events == ["bar:0", "bar:1", "bar:2"]
    assert replay.index == 2 and replay.timestamp == START + timedelta(minutes=2)
    assert not replay.has_next


def test_step_n_equals_repeated_step_one_with_identical_inputs() -> None:
    batched, batched_pipeline = cursor(4)
    single, single_pipeline = cursor(4)
    stepped = batched.step_n(2)
    manual = (single.step_one(), single.step_one())
    assert [view.index for view in stepped] == [0, 1]
    assert [view.current_bar for view in stepped] == [view.current_bar for view in manual]
    assert batched_pipeline.events == single_pipeline.events == ["bar:0", "bar:1"]
    assert batched.index == single.index == 1
    assert batched.timestamp == single.timestamp == START + timedelta(minutes=1)
    rest_batched = batched.step_n(2)
    rest_single = (single.step_one(), single.step_one())
    assert [view.index for view in rest_batched] == [view.index for view in rest_single]
    assert observed(batched_pipeline) == observed(single_pipeline)


def test_run_to_end_equals_repeated_step_one_for_same_data() -> None:
    auto, auto_pipeline = cursor(4)
    manual, manual_pipeline = cursor(4)
    assert auto.run_to_end() == 4
    while manual.has_next:
        manual.step_one()
    assert auto.index == manual.index == 3 and not auto.has_next
    assert observed(auto_pipeline) == observed(manual_pipeline)
    assert auto_pipeline.events == ["bar:0", "bar:1", "bar:2", "bar:3"]


def test_run_to_end_twice_processes_nothing_again() -> None:
    replay, pipeline = cursor(2)
    assert replay.run_to_end() == 2
    assert replay.run_to_end() == 0
    assert replay.run_to_end() == 0
    assert pipeline.events == ["bar:0", "bar:1"]
    assert [view.index for view in pipeline.views] == [0, 1]


def test_run_to_end_from_mid_stream_completes_only_the_remainder() -> None:
    replay, pipeline = cursor(4)
    replay.step_n(1)
    assert replay.run_to_end() == 3
    assert [view.index for view in pipeline.views] == [0, 1, 2, 3]
    assert replay.index == 3 and not replay.has_next


def test_pipeline_failure_mid_step_n_latches_until_reset() -> None:
    class TransientFailingPipeline(RecordingPipeline):
        def __init__(self) -> None:
            super().__init__()
            self.failed_once = False

        def process_bar(self, view: ObservableBars) -> None:
            if view.index == 1 and not self.failed_once:
                self.failed_once = True
                raise RuntimeError("pipeline exploded")
            super().process_bar(view)

    pipeline = TransientFailingPipeline()
    replay = ReplayCursor(SimulationClock(sequence(3)), pipeline)
    with pytest.raises(RuntimeError, match="pipeline exploded"):
        replay.step_n(3)
    assert replay.index == 1
    assert pipeline.events == ["bar:0"]
    assert replay.failed
    with pytest.raises(ReplayCursorError, match="latched"):
        replay.step_one()
    with pytest.raises(ReplayCursorError, match="latched"):
        replay.step_n(0)
    with pytest.raises(ReplayCursorError, match="latched"):
        replay.run_to_end()
    assert replay.index == 1 and pipeline.events == ["bar:0"]
    replay.reset()
    assert not replay.failed and replay.index == -1
    replay.run_to_end()
    assert pipeline.events == ["bar:0", "reset", "bar:0", "bar:1", "bar:2"]


@pytest.mark.parametrize("entrypoint", ["step_one", "run_to_end"])
def test_pipeline_failure_latches_all_stepping_entrypoints(entrypoint: str) -> None:
    class FailingPipeline(RecordingPipeline):
        def process_bar(self, view: ObservableBars) -> None:
            if view.index == 0:
                raise RuntimeError("first bar failed")
            super().process_bar(view)

    pipeline = FailingPipeline()
    replay = ReplayCursor(SimulationClock(sequence(2)), pipeline)
    with pytest.raises(RuntimeError, match="first bar failed"):
        getattr(replay, entrypoint)()
    assert replay.failed and replay.index == 0 and pipeline.events == []
    with pytest.raises(ReplayCursorError, match="latched"):
        replay.step_one()
    assert replay.index == 0 and pipeline.events == []


def test_failed_reset_keeps_cursor_latched_until_pipeline_reset_succeeds() -> None:
    class ResetFailingPipeline(RecordingPipeline):
        def __init__(self) -> None:
            super().__init__()
            self.fail_reset = True

        def process_bar(self, view: ObservableBars) -> None:
            raise RuntimeError("processing failed")

        def reset(self) -> None:
            if self.fail_reset:
                raise RuntimeError("reset failed")
            super().reset()

    pipeline = ResetFailingPipeline()
    replay = ReplayCursor(SimulationClock(sequence(2)), pipeline)
    with pytest.raises(RuntimeError, match="processing failed"):
        replay.step_one()
    with pytest.raises(RuntimeError, match="reset failed"):
        replay.reset()
    assert replay.failed and replay.index == 0
    with pytest.raises(ReplayCursorError, match="latched"):
        replay.run_to_end()
    pipeline.fail_reset = False
    replay.reset()
    assert not replay.failed and replay.index == -1


def test_failed_final_bar_is_not_mistaken_for_successful_exhaustion() -> None:
    class FinalBarFailingPipeline(RecordingPipeline):
        def process_bar(self, view: ObservableBars) -> None:
            if view.index == 1:
                raise RuntimeError("final bar failed")
            super().process_bar(view)

    pipeline = FinalBarFailingPipeline()
    replay = ReplayCursor(SimulationClock(sequence(2)), pipeline)
    with pytest.raises(RuntimeError, match="final bar failed"):
        replay.run_to_end()
    assert replay.index == 1 and not replay.has_next and replay.failed
    assert pipeline.events == ["bar:0"]
    with pytest.raises(ReplayCursorError, match="latched"):
        replay.run_to_end()


def test_step_one_past_final_bar_raises_and_mutates_nothing() -> None:
    replay, pipeline = cursor(2)
    replay.run_to_end()
    with pytest.raises(ReplayCursorError, match="final"):
        replay.step_one()
    assert not replay.failed
    assert replay.index == 1 and len(pipeline.views) == 2
    assert pipeline.events == ["bar:0", "bar:1"]
    with pytest.raises(ReplayCursorError, match="final"):
        replay.step_one()
    assert replay.index == 1 and observed(pipeline) == (
        [0, 1], list(sequence(2).bars), ["bar:0", "bar:1"],
    )


def test_step_n_beyond_remaining_processes_nothing() -> None:
    replay, pipeline = cursor(3)
    with pytest.raises(ReplayCursorError, match="only 3 remain"):
        replay.step_n(4)
    assert replay.index == -1 and pipeline.events == []
    replay.step_one()
    with pytest.raises(ReplayCursorError, match="only 2 remain"):
        replay.step_n(3)
    assert replay.index == 0 and len(pipeline.views) == 1
    assert pipeline.events == ["bar:0"]


def test_step_n_zero_is_a_no_op() -> None:
    replay, pipeline = cursor(2)
    assert replay.step_n(0) == ()
    assert replay.index == -1 and pipeline.events == []
    replay.run_to_end()
    assert replay.step_n(0) == ()
    assert pipeline.events == ["bar:0", "bar:1"]


@pytest.mark.parametrize(("count", "reason"), [
    (-1, "non-negative"),
    (True, "integer"),
    (False, "integer"),
    (1.0, "integer"),
    ("2", "integer"),
    (None, "integer"),
])
def test_step_n_rejects_invalid_counts(count: object, reason: str) -> None:
    replay, pipeline = cursor(3)
    with pytest.raises(ReplayCursorError, match=reason):
        replay.step_n(count)  # type: ignore[arg-type]
    assert replay.index == -1 and pipeline.events == []


def test_reset_then_replay_reproduces_identical_pipeline_events() -> None:
    replay, pipeline = cursor(4)
    replay.run_to_end()
    first_pass = list(pipeline.events)
    assert first_pass == ["bar:0", "bar:1", "bar:2", "bar:3"]
    replay.reset()
    assert replay.index == -1 and replay.timestamp is None and replay.has_next
    assert pipeline.resets == 1 and pipeline.views == []
    replay.run_to_end()
    assert pipeline.events == first_pass + ["reset"] + first_pass
    assert [view.index for view in pipeline.views] == [0, 1, 2, 3]
    assert [view.current_bar for view in pipeline.views] == list(sequence(4).bars)
    replay.reset()
    assert replay.index == -1 and pipeline.resets == 2


def test_full_replay_records_every_index_exactly_once() -> None:
    replay, pipeline = cursor(5)
    replay.run_to_end()
    indexes = [view.index for view in pipeline.views]
    assert indexes == list(range(5))
    assert len(set(indexes)) == len(indexes)


def test_cursor_properties_delegate_to_the_clock() -> None:
    lineage = sequence(3).lineage
    replay, _ = cursor(3)
    assert replay.bar_count == 3 and replay.has_next and replay.index == -1
    assert replay.dataset_revision_id == lineage.dataset_revision_id
    assert replay.source_dataset_id == lineage.source_dataset_id
    assert replay.canonical_checksum == lineage.canonical_checksum
    assert replay.timestamp is None
    replay.step_one()
    assert replay.index == 0 and replay.timestamp == START


def test_constructor_rejects_non_clock_and_non_pipeline() -> None:
    assert isinstance(RecordingPipeline(), AnalyticalPipeline)
    with pytest.raises(ReplayCursorError, match="SimulationClock"):
        ReplayCursor("clock", RecordingPipeline())  # type: ignore[arg-type]
    with pytest.raises(ReplayCursorError, match="AnalyticalPipeline"):
        ReplayCursor(SimulationClock(sequence()), object())  # type: ignore[arg-type]


def test_seek_backward_forward_and_resume_match_uninterrupted_prefix() -> None:
    replay, pipeline = cursor(5)
    replay.run_to_end()
    for target in (2, 4, 0, 3):
        view = replay.seek_to_index(target)
        fresh, fresh_pipeline = cursor(5)
        fresh.step_n(target + 1)
        assert view is not None and view.index == target
        assert replay.index == fresh.index
        assert replay.timestamp == fresh.timestamp
        assert [item.current_bar for item in pipeline.views] == [
            item.current_bar for item in fresh_pipeline.views
        ]
        assert pipeline.events[-(target + 1):] == fresh_pipeline.events
        with pytest.raises(SimulationClockError, match="future"):
            view.bar_at(target + 1)
    replay.run_to_end()
    fresh, fresh_pipeline = cursor(5)
    fresh.run_to_end()
    assert [item.current_bar for item in pipeline.views] == [
        item.current_bar for item in fresh_pipeline.views
    ]
    assert pipeline.events[-5:] == fresh_pipeline.events


def test_seek_from_origin_and_to_pre_first_bar() -> None:
    replay, pipeline = cursor(3)
    assert replay.seek_to_index(-1) is None
    assert pipeline.resets == 0
    first = replay.seek_to_index(0)
    assert first is not None and first.index == 0
    assert pipeline.resets == 0
    assert replay.seek_to_index(0) is first
    assert pipeline.events == ["bar:0"]
    assert replay.seek_to_index(-1) is None
    assert replay.index == -1 and replay.timestamp is None
    assert pipeline.views == [] and pipeline.resets == 1
    assert replay.seek_to_index(-1) is None and pipeline.resets == 1


def test_seek_to_time_requires_exact_aware_bar_and_accepts_timezone_equivalence() -> None:
    replay, pipeline = cursor(3)
    eastern = timezone(timedelta(hours=-4))
    target = (START + timedelta(minutes=2)).astimezone(eastern)
    view = replay.seek_to_time(target)
    assert view.index == 2 and replay.timestamp == START + timedelta(minutes=2)
    assert pipeline.events == ["bar:0", "bar:1", "bar:2"]
    assert replay.seek_to_time(target) is view
    assert pipeline.resets == 0


@pytest.mark.parametrize(("target", "reason"), [
    (-2, "between"),
    (3, "between"),
    (True, "integer"),
    (1.0, "integer"),
    ("1", "integer"),
    (None, "integer"),
])
def test_invalid_seek_index_is_atomic(target: object, reason: str) -> None:
    replay, pipeline = cursor(3)
    replay.step_n(2)
    before = observed(pipeline)
    with pytest.raises(ReplayCursorError, match=reason):
        replay.seek_to_index(target)  # type: ignore[arg-type]
    assert replay.index == 1 and observed(pipeline) == before
    assert pipeline.resets == 0 and not replay.failed


@pytest.mark.parametrize(("target", "reason"), [
    (START.replace(tzinfo=None), "timezone-aware"),
    (START - timedelta(minutes=1), "not an observable bar"),
    (START + timedelta(seconds=30), "not an observable bar"),
    (START + timedelta(minutes=3), "not an observable bar"),
    ("2026-09-27T12:00:00Z", "timezone-aware"),
])
def test_invalid_seek_timestamp_is_atomic(target: object, reason: str) -> None:
    replay, pipeline = cursor(3)
    replay.step_one()
    before = observed(pipeline)
    with pytest.raises(ReplayCursorError, match=reason):
        replay.seek_to_time(target)  # type: ignore[arg-type]
    assert replay.index == 0 and observed(pipeline) == before
    assert pipeline.resets == 0 and not replay.failed


def test_seek_failure_latches_and_requires_explicit_reset() -> None:
    class FailingPipeline(RecordingPipeline):
        def __init__(self) -> None:
            super().__init__()
            self.fail_on_replay = False

        def process_bar(self, view: ObservableBars) -> None:
            if self.fail_on_replay and view.index == 1:
                raise RuntimeError("seek rebuild failed")
            super().process_bar(view)

    pipeline = FailingPipeline()
    replay = ReplayCursor(SimulationClock(sequence(3)), pipeline)
    replay.run_to_end()
    pipeline.fail_on_replay = True
    with pytest.raises(RuntimeError, match="seek rebuild failed"):
        replay.seek_to_index(1)
    assert replay.index == 1 and replay.failed
    with pytest.raises(ReplayCursorError, match="latched"):
        replay.seek_to_index(0)
    with pytest.raises(ReplayCursorError, match="latched"):
        replay.seek_to_time(START)
    pipeline.fail_on_replay = False
    replay.reset()
    assert replay.seek_to_index(2) is not None
    assert not replay.failed and [view.index for view in pipeline.views] == [0, 1, 2]


def test_failed_reset_during_seek_latches_without_replaying() -> None:
    class ResetFailingPipeline(RecordingPipeline):
        def reset(self) -> None:
            raise RuntimeError("seek reset failed")

    pipeline = ResetFailingPipeline()
    replay = ReplayCursor(SimulationClock(sequence(3)), pipeline)
    replay.step_n(2)
    with pytest.raises(RuntimeError, match="seek reset failed"):
        replay.seek_to_index(0)
    assert replay.failed and replay.index == 1
    assert [view.index for view in pipeline.views] == [0, 1]
    with pytest.raises(ReplayCursorError, match="latched"):
        replay.step_one()
