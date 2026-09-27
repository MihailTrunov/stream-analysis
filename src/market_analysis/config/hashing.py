from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from market_analysis.config.models import DetectionAnalysisConfig, EvaluationPlan
from market_analysis.config.resolution import resolve_detection_config, resolve_evaluation_plan
from market_analysis.patterns import ParameterSpec, PatternDefinition

HASH_ALGORITHM = "sha256"
CANONICALIZATION_VERSION = "resolved-normalized-v1"
DETECTION_HASH_VERSION = "detection-config-v1"
EVALUATION_HASH_VERSION = "evaluation-plan-v1"


def detection_config_hash(
    config: DetectionAnalysisConfig,
    *,
    component_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
    pattern_definitions: Mapping[tuple[str, str], PatternDefinition] | None = None,
) -> str:
    resolved = resolve_detection_config(
        config,
        component_parameters=component_parameters,
        pattern_definitions=pattern_definitions,
    )
    return _fingerprint(DETECTION_HASH_VERSION, resolved.canonical_json())


def evaluation_plan_hash(
    plan: EvaluationPlan,
    *,
    outcome_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
    segment_parameters: Mapping[tuple[str, str], tuple[ParameterSpec, ...]] | None = None,
) -> str:
    resolved = resolve_evaluation_plan(
        plan,
        outcome_parameters=outcome_parameters,
        segment_parameters=segment_parameters,
    )
    return _fingerprint(EVALUATION_HASH_VERSION, resolved.canonical_json())


def _fingerprint(version: str, canonical_json: str) -> str:
    return sha256(bytes(f"{version}\n{canonical_json}", "utf-8")).hexdigest()
