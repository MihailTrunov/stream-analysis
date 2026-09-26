"""Immutable analytical configuration contracts."""

from .hashing import detection_config_hash, evaluation_plan_hash
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
from .resolution import resolve_detection_config

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
    "detection_config_hash",
    "evaluation_plan_hash",
    "resolve_detection_config",
]
