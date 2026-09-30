from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from market_analysis import code_version as module
from market_analysis.code_version import CodeCaptureStatus, CodeVersion, capture_code_version
from market_analysis.config import DetectionAnalysisConfig, EvaluationPlan, detection_config_hash
from market_analysis.persistence.runs import create_run_snapshot, load_run_snapshot, metadata

COMMIT = "a" * 40


def test_environment_capture_distinguishes_clean_dirty_and_unknown() -> None:
    for flag, expected in (("false", False), ("true", True), ("", None)):
        result = capture_code_version(
            "build-108",
            environ={"STREAM_ANALYSIS_GIT_COMMIT": COMMIT, "STREAM_ANALYSIS_GIT_DIRTY": flag},
        )
        assert result.revision == COMMIT
        assert result.dirty is expected
        assert result.source == "build-environment"
        assert result.is_clean_committed is (expected is False)


def test_checkout_capture_and_unavailable_are_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {("rev-parse", "--verify", "HEAD"): COMMIT,
               ("status", "--porcelain", "--untracked-files=normal"): " M src/a.py"}
    monkeypatch.setattr(module, "_git", lambda args, repository: answers.get(args))
    dirty = capture_code_version("build-108", environ={}, repository=Path("/unused"))
    assert dirty.status is CodeCaptureStatus.AVAILABLE
    assert dirty.dirty is True
    assert dirty.source == "git-checkout"

    answers.clear()
    unavailable = capture_code_version("build-108", environ={}, repository=Path("/unused"))
    assert unavailable == CodeVersion(
        "build-108", None, None, CodeCaptureStatus.UNAVAILABLE, "git-unavailable"
    )
    assert unavailable.is_clean_committed is False


def test_code_version_requires_consistent_identity() -> None:
    with pytest.raises(ValueError, match="full Git revision"):
        CodeVersion("build", "short", False, CodeCaptureStatus.AVAILABLE, "test")
    with pytest.raises(ValueError, match="cannot claim"):
        CodeVersion("build", COMMIT, False, CodeCaptureStatus.UNAVAILABLE, "test")
    invalid = capture_code_version(
        "build", environ={"STREAM_ANALYSIS_GIT_COMMIT": COMMIT,
                           "STREAM_ANALYSIS_GIT_DIRTY": "maybe"},
    )
    assert invalid.status is CodeCaptureStatus.UNAVAILABLE
    assert invalid.source == "invalid-build-environment"


def test_replay_and_evaluation_persist_injected_code_version_without_changing_hashes() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    config = DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1")
    plan = EvaluationPlan(
        detection_config_hash=detection_config_hash(config), context_schema_version="context-v1"
    )
    seen: list[str] = []

    def provider(build_id: str) -> CodeVersion:
        seen.append(build_id)
        return CodeVersion(build_id, COMMIT, False, CodeCaptureStatus.AVAILABLE, "injected")

    replay_id, evaluation_id = uuid4(), uuid4()
    with engine.begin() as connection:
        replay = create_run_snapshot(
            connection, run_id=replay_id, dataset_revision_id="revision-1",
            calendar_version="calendar-1", build_id="build-1", detection_config=config,
            revision_provider=provider,
        )
        evaluation = create_run_snapshot(
            connection, run_id=evaluation_id, dataset_revision_id="revision-1",
            calendar_version="calendar-1", build_id="build-1", detection_config=config,
            evaluation_plan=plan, revision_provider=provider,
        )
        assert load_run_snapshot(connection, replay_id) == replay
        assert load_run_snapshot(connection, evaluation_id) == evaluation
    assert seen == ["build-1", "build-1"]
    assert replay.code_version == evaluation.code_version
    assert replay.code_version.is_clean_committed
    assert replay.detection_config_hash == evaluation.detection_config_hash

    with engine.begin() as connection:
        dirty = create_run_snapshot(
            connection, run_id=uuid4(), dataset_revision_id="revision-1",
            calendar_version="calendar-1", build_id="build-1", detection_config=config,
            revision_provider=lambda build: CodeVersion(
                build, COMMIT, True, CodeCaptureStatus.AVAILABLE, "injected"
            ),
        )
        unavailable = create_run_snapshot(
            connection, run_id=uuid4(), dataset_revision_id="revision-1",
            calendar_version="calendar-1", build_id="build-1", detection_config=config,
            revision_provider=lambda build: CodeVersion(
                build, None, None, CodeCaptureStatus.UNAVAILABLE, "injected"
            ),
        )
    assert dirty.code_version.dirty is True
    assert unavailable.code_version.status is CodeCaptureStatus.UNAVAILABLE
    assert dirty.detection_config_hash == unavailable.detection_config_hash
    assert dirty.detection_config_hash == replay.detection_config_hash


def test_snapshot_rejects_provider_build_mismatch() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection, pytest.raises(ValueError, match="different build_id"):
        create_run_snapshot(
            connection, run_id=uuid4(), dataset_revision_id="revision-1",
            calendar_version="calendar-1", build_id="build-1",
            detection_config=DetectionAnalysisConfig(instrument_id="US30", calendar_id="demo-v1"),
            revision_provider=lambda _: CodeVersion(
                "wrong-build", COMMIT, False, CodeCaptureStatus.AVAILABLE, "injected"
            ),
        )
