"""Commit browser replay DetectorEvents as durable, reviewable evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from sqlalchemy import Connection

from market_analysis.detection import DetectorEvent, DetectorRuntimeResult
from market_analysis.detection.records import json_value
from market_analysis.domain import Bar
from market_analysis.patterns import PatternDefinition
from market_analysis.persistence.pattern_instances import (
    LifecycleStep,
    PatternInstanceRecord,
    advance_pattern_instance,
    create_pattern_instance,
)


class ReplayEvidenceError(ValueError):
    """Runtime and persisted detector evidence disagree."""


RuntimeKey = tuple[str, str, str]
EventKey = tuple[str, str, str, int]


class ReplayEventStore:
    """Run-local ID bridge; SQL evidence is the authoritative persisted copy."""

    def __init__(self, definitions: Mapping[tuple[str, str], PatternDefinition]) -> None:
        self._definitions = definitions
        self._instances: dict[RuntimeKey, PatternInstanceRecord] = {}
        self._event_ids: dict[EventKey, tuple[str, str]] = {}

    @staticmethod
    def _key(event: DetectorEvent) -> RuntimeKey:
        return event.pattern_id, event.pattern_version, event.instance_id

    def identity(self, event: DetectorEvent) -> tuple[str, str]:
        """Return the persisted event and instance UUIDs for an emitted event."""
        try:
            return self._event_ids[(*self._key(event), event.sequence)]
        except KeyError as exc:
            raise ReplayEvidenceError("detector event was not persisted") from exc

    def __call__(
        self,
        connection: Connection,
        bar: Bar,
        result: DetectorRuntimeResult,
        binding_fingerprint: str,
    ) -> None:
        grouped: dict[RuntimeKey, list[DetectorEvent]] = {}
        for event in result.events:
            grouped.setdefault(self._key(event), []).append(event)
        for instance in result.instances:
            key = (instance.pattern_id, instance.pattern_version, instance.instance_id)
            events = grouped.pop(key, [])
            record = self._instances.get(key)
            if record is None:
                if not events:
                    continue
                definition = self._definitions.get(key[:2])
                if definition is None:
                    raise ReplayEvidenceError("emitted detector definition is not registered")
                opening = events[0]
                ordinal = sum(other[:2] == key[:2] for other in self._instances)
                record = create_pattern_instance(
                    connection,
                    run_id=opening.run_id,
                    definition=definition,
                    binding_fingerprint=binding_fingerprint,
                    occurrence_event_time=opening.event_time,
                    occurrence_detection_time=opening.detection_time,
                    occurrence_ordinal=ordinal,
                    at=bar.timestamp,
                )
            else:
                definition = self._definitions[key[:2]]
            if record.state in definition.effective_terminal_states:
                if events:
                    raise ReplayEvidenceError("terminal detector occurrence emitted another event")
                continue
            steps = tuple(
                LifecycleStep(
                    event.from_state,
                    event.to_state,
                    event.trigger_id,
                    event.event_time,
                    event.detection_time,
                    rationale=cast(Mapping[str, object], json_value(event.rationale)),
                )
                for event in events
            )
            changed = advance_pattern_instance(
                connection,
                record.instance_id,
                definition,
                expected_revision=record.revision,
                bar=bar,
                steps=steps,
                context=instance.context,
            )
            if changed.state != instance.state:
                raise ReplayEvidenceError("persisted lifecycle state differs from runtime")
            if events:
                persisted = changed.detector_events[-len(events) :]
                for source, stored in zip(events, persisted, strict=True):
                    if (
                        source.sequence != stored.sequence
                        or source.trigger_id != stored.transition_reason
                        or source.event_time != stored.event_time
                        or source.detection_time != stored.detection_time
                        or json_value(source.rationale) != json_value(stored.rationale)
                    ):
                        raise ReplayEvidenceError("persisted event differs from runtime")
                    self._event_ids[(*key, source.sequence)] = (stored.event_id, stored.instance_id)
            self._instances[key] = changed
        if grouped:
            raise ReplayEvidenceError("emitted event has no post-step PatternInstance")
