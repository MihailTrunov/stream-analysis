"""SCRUM-65 playback scheduling keeps wall time out of analytical time."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from market_analysis.application.playback import (
    PlaybackController,
    PlaybackError,
    PlaybackMode,
)
from market_analysis.domain import (
    BAR_CHECKSUM_VERSION,
    Bar,
    BarSequence,
    DatasetLineage,
    ObservableBars,
    ReplayCursor,
    SimulationClock,
    Timeframe,
    ValidationStatus,
    canonical_bar_checksum,
)

START = datetime(2026, 1, 5, 12, tzinfo=UTC)


class RecordingPipeline:
    def __init__(self, *, fail_once_at: int | None = None) -> None:
        self.fail_once_at = fail_once_at
        self.indexes: list[int] = []
        self.events: list[tuple[int, str]] = []

    def process_bar(self, view: ObservableBars) -> None:
        if view.index == self.fail_once_at:
            self.fail_once_at = None
            raise RuntimeError("analytical step failed")
        self.indexes.append(view.index)
        if view.index % 2 == 0:
            self.events.append((view.index, "even-bar-event"))

    def reset(self) -> None:
        self.indexes.clear()
        self.events.clear()


def make_controller(
    count: int = 6, *, fail_once_at: int | None = None
) -> tuple[PlaybackController, RecordingPipeline]:
    bars = tuple(
        Bar(
            "US30", Timeframe.M1, START + timedelta(minutes=index),
            Decimal(index + 1), Decimal(index + 2), Decimal(index), Decimal(index + 1),
        )
        for index in range(count)
    )
    lineage = DatasetLineage(
        dataset_revision_id="playback-revision", source_dataset_id="playback-seed",
        instrument_id="US30", timeframe=Timeframe.M1,
        requested_start=START, requested_end=START + timedelta(minutes=count),
        actual_start=START, actual_end=START + timedelta(minutes=count),
        bar_count=count, acquired_at=START, validation_status=ValidationStatus.PASS,
        provider_request_json='{"dataset":"playback-seed"}', source_checksum="a" * 64,
        canonical_checksum=canonical_bar_checksum(bars),
        checksum_version=BAR_CHECKSUM_VERSION, dataset_format_version="parquet-v1",
    )
    pipeline = RecordingPipeline(fail_once_at=fail_once_at)
    cursor = ReplayCursor(SimulationClock(BarSequence(lineage, bars)), pipeline)
    return PlaybackController(cursor), pipeline


def test_manual_slow_fast_and_maximum_speed_have_identical_event_sequence() -> None:
    manual, manual_pipeline = make_controller()
    while manual.cursor.has_next:
        manual.step_one()
    expected_indexes = manual_pipeline.indexes
    expected_events = manual_pipeline.events

    slow, slow_pipeline = make_controller()
    slow.play(Decimal("0.5"))
    for _ in range(6):
        assert slow.tick(timedelta(seconds=2)) == 1

    fast, fast_pipeline = make_controller()
    fast.play(Decimal("2"))
    assert [fast.tick(timedelta(seconds=1)) for _ in range(3)] == [2, 2, 2]

    maximum, maximum_pipeline = make_controller()
    maximum.play_maximum()
    assert maximum.tick() == 6

    for controller, pipeline in (
        (slow, slow_pipeline), (fast, fast_pipeline), (maximum, maximum_pipeline)
    ):
        assert pipeline.indexes == expected_indexes == list(range(6))
        assert pipeline.events == expected_events == [
            (0, "even-bar-event"), (2, "even-bar-event"), (4, "even-bar-event")
        ]
        assert controller.cursor.index == 5
        assert controller.mode is PlaybackMode.PAUSED
        assert controller.tick(timedelta(days=1)) == 0


def test_fractional_credit_pause_resume_and_rate_change_do_not_skip_or_duplicate() -> None:
    controller, pipeline = make_controller()
    controller.play(2)
    assert controller.tick(timedelta(milliseconds=250)) == 0
    controller.play(2)  # repeating the same active rate retains the partial interval
    assert controller.tick(timedelta(milliseconds=250)) == 1
    assert controller.tick(timedelta(milliseconds=250)) == 0
    controller.pause()
    assert controller.mode is PlaybackMode.PAUSED
    assert controller.bars_per_second == Decimal(2)
    assert controller.tick(timedelta(hours=1)) == 0
    assert controller.cursor.index == 0
    controller.resume()
    assert controller.tick(timedelta(milliseconds=250)) == 0
    assert controller.tick(timedelta(milliseconds=250)) == 1
    controller.play(4)  # a rate change clears partial credit without moving a bar
    assert controller.tick(timedelta(milliseconds=125)) == 0
    assert controller.tick(timedelta(milliseconds=125)) == 1
    assert controller.bars_per_second == Decimal(4)
    assert pipeline.indexes == [0, 1, 2]
    assert pipeline.events == [(0, "even-bar-event"), (2, "even-bar-event")]


def test_manual_step_requires_pause_and_preserves_state_across_resume() -> None:
    controller, pipeline = make_controller(3)
    assert controller.mode is PlaybackMode.PAUSED
    with pytest.raises(PlaybackError, match="no playback speed"):
        controller.resume()
    assert controller.step_one().index == 0
    controller.play(1)
    with pytest.raises(PlaybackError, match="paused"):
        controller.step_one()
    assert pipeline.indexes == [0]
    assert controller.tick(timedelta(seconds=1)) == 1
    controller.pause()
    assert controller.step_one().index == 2
    assert pipeline.indexes == [0, 1, 2]


def test_resume_restores_maximum_mode_after_pause_without_mutating_cursor() -> None:
    controller, pipeline = make_controller(3)
    controller.play_maximum()
    controller.pause()
    assert pipeline.indexes == [] and controller.cursor.index == -1
    controller.resume()
    assert controller.mode is PlaybackMode.MAXIMUM
    assert controller.tick() == 3
    assert pipeline.indexes == [0, 1, 2]


def test_maximum_speed_drains_remaining_after_manual_stepping_and_cannot_restart() -> None:
    controller, pipeline = make_controller(4)
    controller.step_one()
    controller.play_maximum()
    assert controller.tick(timedelta(0)) == 3
    assert pipeline.indexes == [0, 1, 2, 3]
    assert controller.mode is PlaybackMode.PAUSED
    with pytest.raises(PlaybackError, match="exhausted"):
        controller.play(1)
    with pytest.raises(PlaybackError, match="exhausted"):
        controller.play_maximum()
    controller.cursor.seek_to_index(1)
    controller.play_maximum()
    assert controller.tick() == 2
    assert pipeline.indexes == [0, 1, 2, 3]


def test_paced_tick_clamps_to_remaining_bars_without_skipping() -> None:
    controller, pipeline = make_controller(3)
    controller.play(100)
    assert controller.tick(timedelta(seconds=1)) == 3
    assert controller.mode is PlaybackMode.PAUSED
    assert pipeline.indexes == [0, 1, 2]
    with pytest.raises(PlaybackError, match="exhausted"):
        controller.resume()


@pytest.mark.parametrize("invalid", [0, -1, Decimal("NaN"), Decimal("Infinity"), True, "2"])
def test_invalid_speed_is_atomic(invalid: object) -> None:
    controller, pipeline = make_controller()
    with pytest.raises(PlaybackError, match="positive finite"):
        controller.play(invalid)  # type: ignore[arg-type]
    assert controller.mode is PlaybackMode.PAUSED
    assert controller.cursor.index == -1 and pipeline.indexes == []


@pytest.mark.parametrize("invalid", [timedelta(microseconds=-1), 1, None])
def test_invalid_elapsed_time_is_atomic(invalid: object) -> None:
    controller, pipeline = make_controller()
    controller.play(1)
    with pytest.raises(PlaybackError, match="non-negative timedelta"):
        controller.tick(invalid)  # type: ignore[arg-type]
    assert controller.mode is PlaybackMode.PACED
    assert controller.cursor.index == -1 and pipeline.indexes == []
    assert controller.tick(timedelta(seconds=1)) == 1


def test_failed_analytical_step_pauses_without_skipping_and_requires_cursor_reset() -> None:
    controller, pipeline = make_controller(fail_once_at=2)
    controller.play(10)
    with pytest.raises(RuntimeError, match="analytical step failed"):
        controller.tick(timedelta(seconds=1))
    assert controller.mode is PlaybackMode.PAUSED
    assert controller.cursor.failed and controller.cursor.index == 2
    assert pipeline.indexes == [0, 1]
    assert controller.tick(timedelta(seconds=1)) == 0
    with pytest.raises(PlaybackError, match="latched"):
        controller.play(1)
    controller.cursor.reset()
    controller.play_maximum()
    assert controller.tick() == 6
    assert pipeline.indexes == list(range(6))


def test_maximum_speed_propagates_analytical_failure_and_pauses() -> None:
    controller, pipeline = make_controller(fail_once_at=2)
    controller.play_maximum()
    with pytest.raises(RuntimeError, match="analytical step failed"):
        controller.tick()
    assert controller.mode is PlaybackMode.PAUSED
    assert controller.cursor.failed and controller.cursor.index == 2
    assert pipeline.indexes == [0, 1]


def test_constructor_rejects_non_cursor() -> None:
    with pytest.raises(PlaybackError, match="ReplayCursor"):
        PlaybackController(object())  # type: ignore[arg-type]
