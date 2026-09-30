"""Frozen detector-authored records: instance state, intents, outputs, events.

Detectors return :class:`DetectorOutput` values and the runtime commits
sequence-pinned :class:`DetectorEvent` records; both are immutable value
objects so a committed bar boundary can never be mutated after the fact.
Persistence schemas for PatternInstance/DetectorEvent belong to SCRUM-81 and
are deliberately not defined here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType


class DetectorRecordError(ValueError):
    """A frozen detector record violates the record contract."""


def freeze_mapping(context: Mapping[str, object]) -> Mapping[str, object]:
    """Validate and detach a record mapping into a read-only copy."""
    if not isinstance(context, Mapping):
        raise DetectorRecordError("record mapping must be a mapping")
    detached: dict[str, object] = {}
    for key, value in context.items():
        if not isinstance(key, str) or not key.strip():
            raise DetectorRecordError("record mapping keys must be non-empty strings")
        detached[key] = _frozen_value(value)
    return MappingProxyType(detached)


def _frozen_value(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DetectorRecordError("record timestamps must be timezone-aware")
        return value
    if value is None or isinstance(value, str | bool | int | Decimal):
        if isinstance(value, Decimal) and not value.is_finite():
            raise DetectorRecordError("record decimals must be finite")
        return value
    if isinstance(value, Mapping):
        return freeze_mapping(value)
    if isinstance(value, tuple | list):
        return tuple(_frozen_value(item) for item in value)
    raise DetectorRecordError(f"unsupported record value: {type(value).__name__}")


def _aware(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise DetectorRecordError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DetectorRecordError(f"{name} must be timezone-aware")
    return value


def _non_empty(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DetectorRecordError(f"{name} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class PatternInstance:
    """In-memory state of one pattern occurrence owned by the runtime.

    ``state`` is a SCRUM-79 lifecycle state and ``context`` carries
    detector-authored structured evidence. The runtime derives ``state`` from
    the lifecycle module and never trusts a detector's claim about it;
    persistence of this record is a SCRUM-81 boundary.
    """

    pattern_id: str
    pattern_version: str
    instance_id: str
    state: str
    context: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("pattern_id", "pattern_version", "instance_id", "state"):
            _non_empty(name, getattr(self, name))
        object.__setattr__(self, "context", freeze_mapping(self.context))

    @property
    def identity(self) -> tuple[str, str, str]:
        """The (pattern_id, pattern_version, instance_id) occurrence key."""
        return self.pattern_id, self.pattern_version, self.instance_id


@dataclass(frozen=True, slots=True)
class TransitionIntent:
    """One ordered lifecycle transition a detector emits for one bar.

    Intents are detector-authored requests only: the runtime validates each
    one against the SCRUM-79 lifecycle rules and commits it as a
    sequence-pinned :class:`DetectorEvent`. ``event_time`` may reference an
    earlier market extreme only when its canonical market event is visible by
    ``detection_time``; the runtime enforces this against the frame feed.
    """

    pattern_id: str
    pattern_version: str
    instance_id: str
    from_state: str
    to_state: str
    trigger_id: str
    event_time: datetime
    detection_time: datetime
    rationale: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "pattern_id",
            "pattern_version",
            "instance_id",
            "from_state",
            "to_state",
            "trigger_id",
        ):
            _non_empty(name, getattr(self, name))
        _aware("event_time", self.event_time)
        _aware("detection_time", self.detection_time)
        object.__setattr__(self, "rationale", freeze_mapping(self.rationale))

    @property
    def identity(self) -> tuple[str, str, str]:
        """The (pattern_id, pattern_version, instance_id) occurrence key."""
        return self.pattern_id, self.pattern_version, self.instance_id


@dataclass(frozen=True, slots=True)
class DetectorOutput:
    """Deterministic result of one detector's processing step.

    ``transitions`` is the ordered intent sequence for the bar (empty means
    the detector observed nothing) and ``instance`` proposes the updated
    occurrence state and context for runtime validation and commit.
    """

    instance: PatternInstance
    transitions: tuple[TransitionIntent, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.instance, PatternInstance):
            raise DetectorRecordError("DetectorOutput requires a PatternInstance")
        transitions = tuple(self.transitions)
        for intent in transitions:
            if not isinstance(intent, TransitionIntent):
                raise DetectorRecordError("transitions must contain TransitionIntent values")
        object.__setattr__(self, "transitions", transitions)


@dataclass(frozen=True, slots=True)
class DetectorEvent:
    """A runtime-committed, sequence-pinned transition ready for persistence.

    Carries the run/dataset/instrument/config lineage plus the pattern
    identity SCRUM-81 needs to persist a DetectorEvent. ``sequence`` is the
    0-based ordinal of the transition within its instance's committed
    history, so a same-bar multi-transition chain stays addressable in order.
    """

    run_id: str
    dataset_revision_id: str
    instrument_id: str
    timeframe: str
    detection_config_hash: str
    pattern_id: str
    pattern_version: str
    instance_id: str
    sequence: int
    from_state: str
    to_state: str
    trigger_id: str
    event_time: datetime
    detection_time: datetime
    rationale: Mapping[str, object]

    def __post_init__(self) -> None:
        for name in (
            "run_id",
            "dataset_revision_id",
            "instrument_id",
            "timeframe",
            "detection_config_hash",
            "pattern_id",
            "pattern_version",
            "instance_id",
            "from_state",
            "to_state",
            "trigger_id",
        ):
            _non_empty(name, getattr(self, name))
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise DetectorRecordError("sequence must be an integer")
        if self.sequence < 0:
            raise DetectorRecordError("sequence must be non-negative")
        _aware("event_time", self.event_time)
        _aware("detection_time", self.detection_time)
        object.__setattr__(self, "rationale", freeze_mapping(self.rationale))

    def to_canonical_dict(self) -> dict[str, object]:
        """Stable canonical payload for byte-identical replay comparisons."""
        return {
            "run_id": self.run_id,
            "dataset_revision_id": self.dataset_revision_id,
            "instrument_id": self.instrument_id,
            "timeframe": self.timeframe,
            "detection_config_hash": self.detection_config_hash,
            "pattern_id": self.pattern_id,
            "pattern_version": self.pattern_version,
            "instance_id": self.instance_id,
            "sequence": self.sequence,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "trigger_id": self.trigger_id,
            "event_time": json_value(self.event_time),
            "detection_time": json_value(self.detection_time),
            "rationale": json_value(self.rationale),
        }


def json_value(value: object) -> object:
    """Convert record values into JSON-stable primitives with UTC timestamps."""
    if isinstance(value, Mapping):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [json_value(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    return value
