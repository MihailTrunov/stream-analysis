from market_analysis.config import (
    ConfigParameter,
    DetectionAnalysisConfig,
    EvaluationPlan,
    OutcomeSelection,
    detection_config_hash,
    evaluation_plan_hash,
)


def test_detection_hash_is_stable_and_evaluation_changes_only_plan_hash() -> None:
    config = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    detection_hash = detection_config_hash(config)
    assert detection_hash == "c6660cf41f67b16e65acd796660721fe0af41e7d99d14d29d9dcd0d694c4db21"
    base = EvaluationPlan(
        detection_config_hash=detection_hash,
        context_schema_version="1",
    )
    changed = EvaluationPlan(
        detection_config_hash=detection_hash,
        context_schema_version="1",
        outcomes=(
            OutcomeSelection(
                outcome_id="forward-return",
                outcome_version="1",
                parameters=(ConfigParameter(name="horizon", value=5),),
            ),
        ),
    )
    assert len(detection_hash) == 64
    assert detection_config_hash(config) == detection_hash
    assert evaluation_plan_hash(base) != evaluation_plan_hash(changed)
    assert detection_config_hash(config) == detection_hash


def test_detection_hash_changes_with_instrument_and_is_order_independent() -> None:
    first = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    second = DetectionAnalysisConfig(instrument_id="DAX", calendar_id="demo-v1")
    assert detection_config_hash(first) != detection_config_hash(second)
