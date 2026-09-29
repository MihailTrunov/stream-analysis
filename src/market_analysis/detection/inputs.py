"""Immutable per-bar input the runtime supplies to one detector binding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from market_analysis.config import DetectionAnalysisConfig
from market_analysis.domain import Bar, Timeframe
from market_analysis.indicators import MarketEvent, MarketStateFrame
from market_analysis.patterns import PatternDefinition

from .records import DetectorRecordError, PatternInstance, freeze_mapping


@dataclass(frozen=True, slots=True)
class DetectorInput:
    """Everything one detector may observe for exactly one completed bar.

    Market-facing content is exactly ``frame`` and its canonical
    ``market_events_this_bar`` feed: detectors consume the feed instead of
    recomputing SwingPoint or SwingStructure break logic, and nothing else
    market-facing is exposed. The remaining fields are immutable run context —
    the detector's own SCRUM-78 definition and resolved parameters, the
    resolved config and its DetectionConfigHash, run/dataset identity, and the
    current in-memory :class:`PatternInstance`.
    """

    frame: MarketStateFrame
    definition: PatternDefinition
    parameters: Mapping[str, object]
    config: DetectionAnalysisConfig
    detection_config_hash: str
    run_id: str
    dataset_revision_id: str
    instance: PatternInstance

    def __post_init__(self) -> None:
        if not isinstance(self.frame, MarketStateFrame):
            raise DetectorRecordError("DetectorInput requires a MarketStateFrame")
        if not isinstance(self.definition, PatternDefinition):
            raise DetectorRecordError("DetectorInput requires a PatternDefinition")
        if not isinstance(self.config, DetectionAnalysisConfig):
            raise DetectorRecordError("DetectorInput requires a DetectionAnalysisConfig")
        if not isinstance(self.instance, PatternInstance):
            raise DetectorRecordError("DetectorInput requires a PatternInstance")
        for name in ("detection_config_hash", "run_id", "dataset_revision_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DetectorRecordError(f"{name} must be a non-empty string")
        if self.frame.run_id != self.run_id:
            raise DetectorRecordError("frame run_id does not match the input run identity")
        if self.frame.dataset_revision_id != self.dataset_revision_id:
            raise DetectorRecordError(
                "frame dataset revision does not match the input dataset revision"
            )
        if self.frame.detection_config_hash != self.detection_config_hash:
            raise DetectorRecordError("frame config hash does not match the input config hash")
        if not self.frame.bar.is_complete:
            raise DetectorRecordError("detector input requires a completed bar")
        if self.frame.bar.instrument_id != self.config.instrument_id:
            raise DetectorRecordError("frame instrument does not match the run config")
        if self.frame.bar.timeframe != self.config.timeframe:
            raise DetectorRecordError("frame timeframe does not match the run config")
        if self.instance.pattern_id != self.definition.pattern_id or (
            self.instance.pattern_version != self.definition.pattern_version
        ):
            raise DetectorRecordError("instance identity does not match the pattern definition")
        if self.instance.state not in self.definition.lifecycle_states:
            raise DetectorRecordError("instance state is not a declared lifecycle state")
        object.__setattr__(self, "parameters", freeze_mapping(self.parameters))

    @property
    def bar(self) -> Bar:
        """The completed canonical bar this input was finalized from."""
        return self.frame.bar

    @property
    def market_events(self) -> tuple[MarketEvent, ...]:
        """The frame's canonical current-bar event feed, in ordinal order."""
        return self.frame.market_events_this_bar

    @property
    def bar_timestamp(self) -> datetime:
        """The completed bar's timestamp."""
        return self.frame.bar.timestamp

    @property
    def detection_time(self) -> datetime:
        """The detection timestamp; for a canonical frame it is the bar close."""
        return self.frame.bar.timestamp

    @property
    def instrument_id(self) -> str:
        return self.config.instrument_id

    @property
    def timeframe(self) -> Timeframe:
        return self.config.timeframe

    @property
    def pattern_id(self) -> str:
        return self.definition.pattern_id

    @property
    def pattern_version(self) -> str:
        return self.definition.pattern_version

    @property
    def completed_bars(self) -> int:
        return self.frame.completed_bars
