"""Closed production registry; unavailable outcome families are not advertised."""

from collections.abc import Mapping
from types import MappingProxyType

from market_analysis.application.excursions import ALGORITHMS, DEFINITIONS
from market_analysis.application.forward_return import (
    FORWARD_RETURN,
    FORWARD_RETURN_PERCENT,
    forward_return,
    forward_return_percent,
)
from market_analysis.application.outcome_evaluation import OutcomeAlgorithm
from market_analysis.application.time_to_threshold import (
    ALGORITHMS as THRESHOLD_ALGORITHMS,
)
from market_analysis.application.time_to_threshold import (
    DEFINITIONS as THRESHOLD_DEFINITIONS,
)
from market_analysis.domain.outcomes import OutcomeDefinition

OUTCOME_DEFINITIONS: Mapping[tuple[str, str], OutcomeDefinition] = MappingProxyType(
    {
        definition.identity: definition
        for definition in (
            FORWARD_RETURN,
            FORWARD_RETURN_PERCENT,
            *DEFINITIONS,
            *THRESHOLD_DEFINITIONS,
        )
    }
)
OUTCOME_ALGORITHMS: Mapping[tuple[str, str], OutcomeAlgorithm] = MappingProxyType(
    {
        **ALGORITHMS,
        **THRESHOLD_ALGORITHMS,
        FORWARD_RETURN.identity: forward_return,
        FORWARD_RETURN_PERCENT.identity: forward_return_percent,
    }
)
