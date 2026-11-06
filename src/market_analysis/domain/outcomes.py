"""Versioned downstream outcome contracts. Detector execution never imports these."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Self, cast

from pydantic import (
    Field,
    StrictBool,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)

from market_analysis.config import EvaluationPlan, OutcomeSelection, resolve_evaluation_plan
from market_analysis.config.models import Identifier, ImmutableModel
from market_analysis.patterns import ParameterSpec

from .aggregation import AGGREGATION_VERSION
from .market_data import Timeframe


class OutcomeError(ValueError):
    """An outcome contract, window, identity or typed result is invalid."""


class OutputType(StrEnum):
    NUMERIC = "NUMERIC"
    BOOLEAN = "BOOLEAN"
    CATEGORICAL = "CATEGORICAL"
    EVENT_REFERENCE = "EVENT_REFERENCE"


class ResultStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    AMBIGUOUS = "AMBIGUOUS"


class ReferenceMode(StrEnum):
    FROM_DETECTION_TIME = "FROM_DETECTION_TIME"
    FROM_EVENT_TIME = "FROM_EVENT_TIME"


def canonical(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value: object, domain: str) -> str:
    return sha256(f"{domain}\n{canonical(value)}".encode()).hexdigest()


class OutcomeDefinition(ImmutableModel):
    outcome_id: Identifier
    outcome_version: Identifier
    output_type: OutputType
    parameters: tuple[ParameterSpec, ...]
    horizon_parameter: Identifier = "horizon"
    reference_mode: ReferenceMode = ReferenceMode.FROM_DETECTION_TIME
    retrospective_label: str | None = None
    window_policy: Literal["m1-same-trading-day-v1"] = "m1-same-trading-day-v1"
    reference_price: Literal["COMPLETED_REFERENCE_CLOSE"] = "COMPLETED_REFERENCE_CLOSE"
    units: Identifier
    normalization: Identifier = "NONE"
    direction_convention: Identifier = "INSTRUMENT_PRICE"
    eligible_event_kinds: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[str, str]:
        return self.outcome_id, self.outcome_version

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        names = [item.parameter_id for item in self.parameters]
        if len(set(names)) != len(names) or self.horizon_parameter not in names:
            raise OutcomeError("outcome needs unique parameters including its horizon")
        if self.reference_mode is ReferenceMode.FROM_EVENT_TIME and (
            not self.retrospective_label or not self.retrospective_label.strip()
        ):
            raise OutcomeError("event-time outcomes require a retrospective/descriptive label")
        if self.output_type is OutputType.CATEGORICAL and (
            not self.categories
            or len(set(self.categories)) != len(self.categories)
            or any(not item.strip() for item in self.categories)
        ):
            raise OutcomeError("categorical outcomes require declared unique categories")
        return self

    def resolve(self, selection: OutcomeSelection) -> OutcomeSelection:
        if (selection.outcome_id, selection.outcome_version) != self.identity:
            raise OutcomeError("outcome selection does not match definition")
        plan = resolve_evaluation_plan(
            EvaluationPlan(
                detection_config_hash="0" * 64,
                context_schema_version="unused",
                outcomes=(selection,),
            ),
            outcome_parameters={self.identity: self.parameters},
        )
        resolved = plan.outcomes[0]
        horizon = next(
            item.value for item in resolved.parameters if item.name == self.horizon_parameter
        )
        if type(horizon) is not int or not 1 <= horizon <= 100_000:
            raise OutcomeError("outcome horizon must be 1..100000 canonical M1 bars")
        return resolved

    def fingerprint(self) -> str:
        payload = self.model_dump(mode="json", exclude={"parameters"})
        payload["parameters"] = [
            item.canonical(include_default=True, include_description=False)
            for item in self.parameters
        ]
        return digest(payload, "outcome-definition-v1")


class OutcomeEventReference(ImmutableModel):
    semantic_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: Literal["market", "detector"]
    dataset_revision_id: Identifier
    instrument_id: Identifier
    timeframe: Timeframe
    detection_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_time: datetime
    detection_time: datetime
    available_at: datetime
    within_bar_ordinal: int = Field(ge=0, strict=True)

    @field_validator("event_time", "detection_time", "available_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise OutcomeError("outcome event timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        if self.event_time > self.detection_time or self.detection_time >= self.available_at:
            raise OutcomeError("outcome event reference times are inconsistent")
        return self

    @property
    def observable_order(self) -> tuple[datetime, int, int]:
        return self.available_at, 0 if self.kind == "market" else 1, self.within_bar_ordinal


class ObservationWindow(ImmutableModel):
    policy: Literal["m1-same-trading-day-v1"] = "m1-same-trading-day-v1"
    reference_mode: ReferenceMode
    retrospective_label: str | None = None
    reference_time: datetime
    end: datetime
    horizon_bars: int = Field(ge=1, strict=True)
    observed_bars: int = Field(ge=0, strict=True)
    reference_price: Decimal

    @field_validator("reference_time", "end")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise OutcomeError("window timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.end < self.reference_time or not self.reference_price.is_finite():
            raise OutcomeError("invalid outcome window/reference price")
        if self.observed_bars > self.horizon_bars:
            raise OutcomeError("window has too many observations")
        return self


class OutcomeLineage(ImmutableModel):
    source_event_id: Identifier
    source_run_id: Identifier
    context_snapshot_id: Identifier
    source_event_semantic_ref: str = Field(pattern=r"^[0-9a-f]{64}$")
    instance_semantic_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_revision_id: Identifier
    source_dataset_id: Identifier
    canonical_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument_id: Identifier
    source_timeframe: Timeframe
    source_resolution: Literal["1m"] = "1m"
    aggregation_version: str = AGGREGATION_VERSION
    calendar_version: Identifier
    detection_config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluation_plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    definition_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_schema_version: Identifier
    build_id: Identifier
    code_revision: str | None
    code_dirty: bool | None
    code_capture_status: Identifier


class OutcomeObservation(ImmutableModel):
    schema_version: Literal["outcome-observation-v1"] = "outcome-observation-v1"
    observation_id: Identifier
    semantic_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_id: Identifier
    outcome_version: Identifier
    configuration: OutcomeSelection
    output_type: OutputType
    status: ResultStatus
    value: Decimal | StrictBool | StrictStr | OutcomeEventReference | None = None
    reason: str | None = None
    window: ObservationWindow
    lineage: OutcomeLineage

    @field_serializer("configuration", when_used="json")
    def configuration_payload(self, value: OutcomeSelection) -> object:
        return selection_payload(value)

    @field_validator("configuration", mode="before")
    @classmethod
    def decode_configuration(cls, value: object) -> object:
        if isinstance(value, dict):
            raw = EvaluationPlan(
                detection_config_hash="0" * 64, context_schema_version="unused"
            ).canonical_dict()
            raw["outcomes"] = [value]
            return EvaluationPlan.from_canonical_json(canonical(raw)).outcomes[0]
        return value

    @model_validator(mode="before")
    @classmethod
    def reject_float(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("value"), float):
            raise OutcomeError("numeric outcome values require finite Decimal, not float")
        if (
            isinstance(value, dict)
            and value.get("output_type") == OutputType.NUMERIC
            and (isinstance(value.get("value"), str))
        ):
            value = {**value, "value": Decimal(value["value"])}
        return value

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.status is not ResultStatus.AVAILABLE:
            if self.value is not None or not self.reason:
                raise OutcomeError("unavailable outcomes require a reason and no zero-filled value")
        else:
            valid = {
                OutputType.NUMERIC: isinstance(self.value, Decimal) and self.value.is_finite(),
                OutputType.BOOLEAN: type(self.value) is bool,
                OutputType.CATEGORICAL: isinstance(self.value, str) and bool(self.value.strip()),
                OutputType.EVENT_REFERENCE: isinstance(self.value, OutcomeEventReference),
            }[self.output_type]
            if not valid:
                raise OutcomeError("outcome value does not match its declared output type")
            if self.window.observed_bars != self.window.horizon_bars:
                raise OutcomeError("available outcome requires its complete observation window")
        if (self.configuration.outcome_id, self.configuration.outcome_version) != (
            self.outcome_id,
            self.outcome_version,
        ):
            raise OutcomeError("outcome configuration identity mismatch")
        if self.semantic_key != outcome_semantic_key(
            self.lineage.source_event_semantic_ref, self.configuration
        ):
            raise OutcomeError("outcome semantic identity mismatch")
        return self

    def canonical_json(self) -> str:
        return canonical(self.model_dump(mode="json"))


def outcome_semantic_key(source_event_semantic_ref: str, selection: OutcomeSelection) -> str:
    return digest(
        {
            "source_event_semantic_ref": source_event_semantic_ref,
            "configuration": selection_payload(selection),
        },
        "outcome-observation-v1",
    )


def selection_payload(selection: OutcomeSelection) -> object:
    payload = EvaluationPlan(
        detection_config_hash="0" * 64, context_schema_version="unused", outcomes=(selection,)
    ).canonical_dict()["outcomes"]
    return cast(list[object], payload)[0]
