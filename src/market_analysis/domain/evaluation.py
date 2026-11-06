"""Immutable multi-stream evaluation input and explicit stage/lifecycle identity."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from market_analysis.config import DetectionAnalysisConfig, EvaluationPlan, EventSetSelection
from market_analysis.config.models import Identifier, ImmutableModel

from .market_data import Timeframe
from .outcomes import canonical, digest


class EvaluationError(ValueError):
    """An evaluation input, primary population or lifecycle transition is invalid."""


class EvaluationNotFound(EvaluationError):
    """The requested durable evaluation does not exist."""


class EvaluationStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    DETECTION_COMPLETED = "DETECTION_COMPLETED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RequestedEvaluationStream(ImmutableModel):
    config: DetectionAnalysisConfig
    plan: EvaluationPlan


class EvaluationRequest(ImmutableModel):
    dataset_revision_id: Identifier
    instrument_id: Identifier
    selected_start: datetime
    selected_end: datetime
    streams: tuple[RequestedEvaluationStream, ...] = Field(min_length=1, max_length=7)

    @field_validator("selected_start", "selected_end")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise EvaluationError("evaluation boundaries must be timezone-aware")
        if value.second or value.microsecond:
            raise EvaluationError("evaluation boundaries must be minute-aligned")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.selected_end <= self.selected_start:
            raise EvaluationError("evaluation end must follow start")
        if any(item.config.instrument_id != self.instrument_id for item in self.streams):
            raise EvaluationError("all evaluation streams must use the selected instrument")
        timeframes = [item.config.timeframe for item in self.streams]
        if len(set(timeframes)) != len(timeframes):
            raise EvaluationError("copied evaluation settings require one selection per timeframe")
        return self


class EvaluationStreamInput(ImmutableModel):
    run_id: UUID
    timeframe: Timeframe
    pattern_id: Identifier
    pattern_version: Identifier
    definition_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_json: str
    detection_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_json: str
    evaluation_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_definitions: tuple[str, ...] = ()
    warmup_required: int = Field(ge=0, strict=True)
    warmup_available: int = Field(ge=0, strict=True)
    expected_bars: int = Field(ge=1, strict=True)
    expected_last_available_at: datetime


class EvaluationInput(ImmutableModel):
    schema_version: Literal["evaluation-run-v1"] = "evaluation-run-v1"
    evaluation_id: UUID
    dataset_revision_id: Identifier
    source_dataset_id: Identifier
    canonical_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument_id: Identifier
    source_resolution: Literal["1m"] = "1m"
    source_bar_count: int = Field(ge=1, strict=True)
    execution_bar_count: int = Field(ge=1, strict=True)
    initialization_origin: datetime
    selected_start: datetime
    selected_end: datetime
    calendar_id: Identifier
    calendar_version: Identifier
    aggregation_version: Literal["m1-aggregation-v1"] = "m1-aggregation-v1"
    build_id: Identifier
    code_revision: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    code_dirty: Literal[False] = False
    code_capture_status: Literal["available"] = "available"
    code_capture_source: Identifier
    original_configs: tuple[str, ...]
    original_plans: tuple[str, ...]
    streams: tuple[EvaluationStreamInput, ...] = Field(min_length=1, max_length=21)

    @field_validator("initialization_origin", "selected_start", "selected_end")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise EvaluationError("pinned evaluation times must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if not self.initialization_origin <= self.selected_start < self.selected_end:
            raise EvaluationError("invalid evaluation initialization/selected interval")
        if self.execution_bar_count > self.source_bar_count:
            raise EvaluationError("evaluation execution exceeds immutable source coverage")
        keys = [(item.timeframe, item.pattern_id, item.pattern_version) for item in self.streams]
        if len(set(keys)) != len(keys) or len({item.run_id for item in self.streams}) != len(keys):
            raise EvaluationError("duplicate evaluation analytical stream")
        if any(item.warmup_available < item.warmup_required for item in self.streams):
            raise EvaluationError("evaluation input has insufficient stream warmup")
        return self

    @property
    def semantic_key(self) -> str:
        payload = self.model_dump(mode="json", exclude={"evaluation_id", "code_capture_source"})
        payload["streams"] = [
            item.model_dump(mode="json", exclude={"run_id"}) for item in self.streams
        ]
        return digest(payload, "evaluation-run-v1")

    @property
    def event_set_id(self) -> str:
        streams = []
        for stream in self.streams:
            plan = EvaluationPlan.from_canonical_json(stream.plan_json)
            streams.append(
                {
                    "detection_config_hash": stream.detection_config_hash,
                    "definition_fingerprint": stream.definition_fingerprint,
                    "binding_fingerprint": stream.binding_fingerprint,
                    "context_schema_version": plan.context_schema_version,
                    "selection": (plan.event_set or EventSetSelection()).canonical_dict(),
                }
            )
        payload = self.model_dump(
            mode="json",
            include={
                "dataset_revision_id",
                "canonical_checksum",
                "initialization_origin",
                "selected_start",
                "selected_end",
                "calendar_version",
                "aggregation_version",
                "build_id",
                "code_revision",
            },
        )
        payload["streams"] = streams
        return digest(payload, "evaluation-event-set-v1")

    def canonical_json(self) -> str:
        return canonical(self.model_dump(mode="json"))
