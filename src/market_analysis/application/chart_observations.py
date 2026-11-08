"""Bounded chart projections with lazy access to complete canonical audit frames."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import cached_property
from typing import Any

from market_analysis.indicators.market_state import MarketStateFrame


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _scalars(values: Mapping[str, object]) -> dict[str, object]:
    """Do not descend into cumulative arrays, nested evidence or component lineage."""
    result: dict[str, object] = {}
    for key, value in values.items():
        if isinstance(value, datetime):
            result[key] = _stamp(value)
        elif isinstance(value, Decimal):
            result[key] = format(value, 'f')
        elif value is None or isinstance(value, str | bool | int):
            result[key] = value
    return result


@dataclass(frozen=True)
class RecordedFrame(Mapping[str, Any]):
    """Retain the runtime's immutable frame; chart reads never materialize debug histories.

    Mapping access preserves the complete observation contract used by audit/parity
    consumers. The API uses chart_payload explicitly, not that debug representation.
    """

    frame: MarketStateFrame
    available_at: datetime

    @cached_property
    def canonical(self) -> dict[str, Any]:
        value = json.loads(self.frame.debug_json())
        return {'timestamp': _stamp(self.frame.bar.timestamp),
                'available_at': _stamp(self.available_at), 'availability': value['availability'],
                'components': value['components'], 'market_events': value['market_events_this_bar']}

    def __getitem__(self, key: str) -> Any:
        if key == 'timestamp':
            return _stamp(self.frame.bar.timestamp)
        if key == 'available_at':
            return _stamp(self.available_at)
        return self.canonical[key]

    def __iter__(self) -> Iterator[str]:
        return iter(('timestamp', 'available_at', 'availability', 'components', 'market_events'))

    def __len__(self) -> int:
        return 5

    def chart_payload(self) -> dict[str, Any]:
        components = {}
        for key, values in self.frame.components.items():
            projected = _scalars(values)
            leg = values.get('active_leg')
            if isinstance(leg, Mapping):
                projected['active_leg'] = _scalars(leg)
            components[key] = projected
        return {'timestamp': _stamp(self.frame.bar.timestamp),
                'available_at': _stamp(self.available_at),
                'availability': dict(self.frame.availability), 'components': components,
                'market_events': [
                    {'ordinal': event.ordinal, 'event_type': event.event_type.value,
                     'event_time': _stamp(event.event_time),
                     'detection_time': _stamp(event.detection_time),
                     'source_instance_id': event.source_instance_id,
                     'evidence': {name: getattr(event.evidence, name)
                                  for name in ('swing_type', 'label')
                                  if hasattr(event.evidence, name)}}
                    for event in self.frame.market_events_this_bar
                ]}
