"""Production MVP segment contracts; arbitrary expressions are not registered."""

from collections.abc import Mapping
from types import MappingProxyType

from market_analysis.domain.segments import SegmentDefinition

SEGMENT_DEFINITIONS: Mapping[tuple[str, str], SegmentDefinition] = MappingProxyType(
    {
        definition.identity: definition
        for definition in (
            SegmentDefinition(
                segment_id="BY_INSTRUMENT", segment_version="1", fields=("instrument_id",)
            ),
            SegmentDefinition(
                segment_id="BY_SESSION", segment_version="1", fields=("session_name",)
            ),
            SegmentDefinition(
                segment_id="BY_INSTRUMENT_SESSION",
                segment_version="1",
                fields=("instrument_id", "session_name"),
            ),
        )
    }
)
SEGMENT_PARAMETERS = MappingProxyType(
    {key: definition.parameters for key, definition in SEGMENT_DEFINITIONS.items()}
)
