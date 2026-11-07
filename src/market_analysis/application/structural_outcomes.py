"""Next canonical structural events, never inferred from later state snapshots."""

from collections.abc import Mapping
from decimal import Decimal
from functools import partial
from typing import Literal, cast

from market_analysis.application.outcome_evaluation import (
    OutcomeAlgorithm,
    OutcomeInput,
    OutcomeResult,
)
from market_analysis.domain.outcomes import (
    OutcomeDefinition,
    OutcomeError,
    OutputType,
    ResultStatus,
)
from market_analysis.patterns import ParameterSpec, ParameterType

Family = Literal["SWING", "BREAK", "TREND_LEG"]
FAMILIES: tuple[Family, ...] = ("SWING", "BREAK", "TREND_LEG")
TYPES = {
    "SWING": ("SWING_POINT_CONFIRMED",),
    "BREAK": ("SWING_HIGH_CLOSE_BREAK", "SWING_LOW_CLOSE_BREAK"),
    "TREND_LEG": (
        "TREND_LEG_STARTED",
        "TREND_LEG_ENDED",
        "TREND_LEG_PROTECTION_ADVANCED",
        "TREND_LEG_QUALIFIED",
        "TREND_LEG_QUALIFICATION_ENDED",
    ),
}
HORIZON = ParameterSpec(
    "horizon", ParameterType.INTEGER, 5, minimum=Decimal(1), maximum=Decimal(100000)
)
INSTANCE = ParameterSpec("source_instance_id", ParameterType.STRING, "ANY")
TRANSITION = ParameterSpec(
    "transition", ParameterType.STRING, "ANY", supported_values=("ANY", *TYPES["TREND_LEG"])
)


def _definition(family: Family, reference: bool) -> OutcomeDefinition:
    prefix = {
        "SWING": "NEXT_CONFIRMED_SWING",
        "BREAK": "FIRST_STRUCTURE_BREAK",
        "TREND_LEG": "NEXT_TREND_LEG_TRANSITION",
    }[family]
    categories = {
        "SWING": ("HIGH", "LOW"),
        "BREAK": ("UP", "DOWN"),
        "TREND_LEG": tuple(
            f"{kind}_{direction}" for kind in TYPES["TREND_LEG"] for direction in ("UP", "DOWN")
        ),
    }[family]
    return OutcomeDefinition(
        outcome_id=f"{prefix}_REFERENCE" if reference else prefix,
        outcome_version="1",
        output_type=OutputType.EVENT_REFERENCE if reference else OutputType.CATEGORICAL,
        parameters=(HORIZON, INSTANCE, TRANSITION)
        if family == "TREND_LEG"
        else (HORIZON, INSTANCE),
        units="canonical_event" if reference else "category",
        direction_convention="CANONICAL_EVENT_EVIDENCE",
        categories=() if reference else (*categories, "NO_EVENT"),
    )


DEFINITIONS = tuple(
    _definition(family, reference) for family in FAMILIES for reference in (False, True)
)
_BY_KEY = {
    (family, reference): _definition(family, reference)
    for family in FAMILIES
    for reference in (False, True)
}


def structural_outcome(
    source: OutcomeInput, *, family: Family, reference: bool = False
) -> OutcomeResult:
    definition = _BY_KEY[(family, reference)]
    if source.configuration is None:
        raise OutcomeError("structural outcome requires its resolved configuration")
    parameters = {
        item.name: item.value for item in definition.resolve(source.configuration).parameters
    }
    if (
        len(source.bars) != source.window.horizon_bars
        or source.window.observed_bars != source.window.horizon_bars
    ):
        return OutcomeResult(
            ResultStatus.INSUFFICIENT_DATA, reason="complete forward window required"
        )
    components = source.context.get("components")
    if not isinstance(components, Mapping):
        return OutcomeResult(
            ResultStatus.NOT_APPLICABLE, reason="source has no captured component configuration"
        )
    required = {"SWING": "swing_point", "BREAK": "swing_structure", "TREND_LEG": "trend_leg"}[
        family
    ]
    required_families: tuple[str, ...] = (required,)
    if family == "TREND_LEG":
        qualification_types = ("TREND_LEG_QUALIFIED", "TREND_LEG_QUALIFICATION_ENDED")
        if parameters["transition"] in qualification_types:
            required_families = ("trend_leg_qualification",)
        elif parameters["transition"] == "ANY":
            required_families = ("trend_leg", "trend_leg_qualification")
    matching = any(
        isinstance(value, Mapping)
        and (
            value.get("component_id") in required_families
            or (
                required == "swing_structure" and value.get("definition_id") == "SWING_STRUCTURE_V1"
            )
        )
        and (parameters["source_instance_id"] == "ANY" or key == parameters["source_instance_id"])
        for key, value in components.items()
    )
    if not matching:
        return OutcomeResult(
            ResultStatus.NOT_APPLICABLE,
            reason="required structural component instance was not configured",
        )
    selected = next(
        (
            event
            for event in sorted(source.events, key=lambda item: item.observable_order)
            if event.kind == "market"
            and event.canonical_market is not None
            and event.canonical_market.event_type in TYPES[family]
            and (
                parameters["source_instance_id"] == "ANY"
                or event.canonical_market.source_instance_id == parameters["source_instance_id"]
            )
            and (
                family != "TREND_LEG"
                or parameters["transition"] == "ANY"
                or event.canonical_market.event_type == parameters["transition"]
            )
        ),
        None,
    )
    if selected is None:
        return (
            OutcomeResult(
                ResultStatus.NOT_APPLICABLE,
                reason="NO_EVENT: no qualifying canonical event in the complete window",
            )
            if reference
            else OutcomeResult(ResultStatus.AVAILABLE, "NO_EVENT")
        )
    assert selected.canonical_market is not None
    category = selected.canonical_market.category
    category_definition = _BY_KEY[(family, False)]
    if category not in category_definition.categories or category == "NO_EVENT":
        raise OutcomeError("canonical structural event has an invalid category")
    return OutcomeResult(ResultStatus.AVAILABLE, selected if reference else category)


ALGORITHMS: Mapping[tuple[str, str], OutcomeAlgorithm] = {
    _BY_KEY[(family, reference)].identity: cast(
        OutcomeAlgorithm, partial(structural_outcome, family=family, reference=reference)
    )
    for family in FAMILIES
    for reference in (False, True)
}
