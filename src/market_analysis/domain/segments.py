"""Versioned closed MVP partitions of immutable detection-time source context."""

from typing import Literal, Self

from pydantic import Field, model_validator

from market_analysis.config import EvaluationPlan, SegmentSelection, resolve_evaluation_plan
from market_analysis.config.models import Identifier, ImmutableModel
from market_analysis.domain.outcomes import digest
from market_analysis.patterns import ParameterSpec

SegmentField = Literal["instrument_id", "session_name"]


class SegmentDefinition(ImmutableModel):
    segment_id: Identifier
    segment_version: Identifier
    fields: tuple[SegmentField, ...]
    allowed_values: Literal["OBSERVED_CAPTURED_VALUES"] = "OBSERVED_CAPTURED_VALUES"
    unavailable_policy: Literal["EXPLICIT_UNAVAILABLE_BUCKET"] = "EXPLICIT_UNAVAILABLE_BUCKET"
    overlap_policy: Literal["NON_OVERLAPPING"] = "NON_OVERLAPPING"
    parameters: tuple[ParameterSpec, ...] = ()

    @model_validator(mode="after")
    def valid_fields(self) -> Self:
        if not self.fields or len(set(self.fields)) != len(self.fields):
            raise ValueError("segment fields must be nonempty and unique")
        return self

    @property
    def identity(self) -> tuple[str, str]:
        return self.segment_id, self.segment_version

    def resolve(self, selection: SegmentSelection) -> SegmentSelection:
        if (selection.segment_id, selection.segment_version) != self.identity:
            raise ValueError("segment selection does not match definition")
        return resolve_evaluation_plan(
            EvaluationPlan(
                detection_config_hash="0" * 64,
                context_schema_version="unused",
                segments=(selection,),
            ),
            segment_parameters={self.identity: self.parameters},
        ).segments[0]

    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"), "segment-definition-v1")


class SegmentValue(ImmutableModel):
    field: SegmentField
    value: Identifier | None
    availability: Literal["AVAILABLE", "UNAVAILABLE"]

    @model_validator(mode="after")
    def valid_availability(self) -> Self:
        if (self.value is None) != (self.availability == "UNAVAILABLE"):
            raise ValueError("segment availability must match its captured value")
        return self


class SegmentBucket(ImmutableModel):
    segment_id: Identifier
    segment_version: Identifier
    definition_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    values: tuple[SegmentValue, ...]

    @property
    def semantic_key(self) -> str:
        return digest(self.model_dump(mode="json"), "segment-bucket-v1")
