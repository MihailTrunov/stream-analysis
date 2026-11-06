"""The shared completed-candle execution/persistence boundary for chart and worker modes."""

from sqlalchemy import Connection

from market_analysis.application.replay_event_store import ReplayEventStore
from market_analysis.detection import DetectorRuntime, DetectorRuntimeResult
from market_analysis.domain.aggregation import AnalyticalCandle


def process_detector_candle(
    connection: Connection,
    candle: AnalyticalCandle,
    runtime: DetectorRuntime,
    event_store: ReplayEventStore,
) -> DetectorRuntimeResult:
    result = runtime.process_bar(candle.bar)
    event_store(
        connection,
        candle.bar,
        result,
        runtime.binding_fingerprint,
        available_at=candle.available_at,
    )
    return result
