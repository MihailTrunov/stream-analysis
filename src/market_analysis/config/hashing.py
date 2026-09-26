from __future__ import annotations

from hashlib import sha256

from market_analysis.config.models import DetectionAnalysisConfig, EvaluationPlan

DETECTION_HASH_VERSION = "detection-config-v1"
EVALUATION_HASH_VERSION = "evaluation-plan-v1"


def detection_config_hash(config: DetectionAnalysisConfig) -> str:
    return _fingerprint(DETECTION_HASH_VERSION, config.canonical_json())


def evaluation_plan_hash(plan: EvaluationPlan) -> str:
    return _fingerprint(EVALUATION_HASH_VERSION, plan.canonical_json())


def _fingerprint(version: str, canonical_json: str) -> str:
    return sha256(f"{version}\n{canonical_json}".encode()).hexdigest()
