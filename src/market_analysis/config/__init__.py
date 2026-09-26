"""Immutable analytical configuration contracts."""

from .models import (
    ComponentSelection,
    ConfigParameter,
    ConfigurationError,
    ConfigurationPresetRevision,
    DetectionAnalysisConfig,
    EvaluationPlan,
    OutcomeSelection,
    PatternSelection,
    SegmentSelection,
    StalePresetRevisionError,
)

__all__ = [
    "ComponentSelection",
    "ConfigParameter",
    "ConfigurationError",
    "ConfigurationPresetRevision",
    "DetectionAnalysisConfig",
    "EvaluationPlan",
    "OutcomeSelection",
    "PatternSelection",
    "SegmentSelection",
    "StalePresetRevisionError",
]
