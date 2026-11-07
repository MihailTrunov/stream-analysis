"""Production v1 forward returns over the framework's bounded canonical-M1 window."""

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import cast

from market_analysis.application.outcome_evaluation import OutcomeInput, OutcomeResult
from market_analysis.config import ConfigParameter, OutcomeSelection
from market_analysis.domain.outcomes import (
    OutcomeDefinition,
    OutcomeError,
    OutputType,
    ResultStatus,
)
from market_analysis.patterns import ParameterSpec, ParameterType

DEFAULT_HORIZONS = (5, 15, 30, 60)
PARAMETERS = (
    ParameterSpec("horizon", ParameterType.INTEGER, 5, minimum=Decimal(1), maximum=Decimal(100000)),
    ParameterSpec("directional", ParameterType.BOOLEAN, False),
)
FORWARD_RETURN = OutcomeDefinition(
    outcome_id="FORWARD_RETURN",
    outcome_version="1",
    output_type=OutputType.NUMERIC,
    parameters=PARAMETERS,
    units="points",
    direction_convention="CAPTURED_MVP_DIRECTION_IF_SELECTED",
)
FORWARD_RETURN_PERCENT = OutcomeDefinition(
    outcome_id="FORWARD_RETURN_PERCENT",
    outcome_version="1",
    output_type=OutputType.NUMERIC,
    parameters=PARAMETERS,
    units="ratio",
    normalization="REFERENCE_PRICE_DECIMAL28_HALF_EVEN",
    direction_convention="CAPTURED_MVP_DIRECTION_IF_SELECTED",
)
_DIRECTION_FIELDS = {
    ("EMA_STRUCTURE_REVERSAL_V1", "1"): "candidate_direction",
    ("EMA_RECLAIM_STRUCTURE_CONTINUATION_V1", "1"): "source_direction",
}


def _direction(source: OutcomeInput) -> str | None:
    detector = source.context.get("detector")
    if not isinstance(detector, Mapping) or detector.get("context_status") != "AVAILABLE":
        return None
    pattern_id, version = detector.get("pattern_id"), detector.get("pattern_version")
    if not isinstance(pattern_id, str) or not isinstance(version, str):
        return None
    field = _DIRECTION_FIELDS.get((pattern_id, version))
    context = detector.get("context")
    if field is None or not isinstance(context, Mapping):
        return None
    direction = context.get(field)
    return "UP" if direction == "UP" else "DOWN" if direction == "DOWN" else None


def _calculate(
    source: OutcomeInput, definition: OutcomeDefinition, *, percent: bool
) -> OutcomeResult:
    if source.configuration is None:
        raise OutcomeError("forward return requires its resolved outcome configuration")
    selected = definition.resolve(source.configuration)
    directional = next(item.value for item in selected.parameters if item.name == "directional")
    if (
        len(source.bars) != source.window.horizon_bars
        or source.window.observed_bars != source.window.horizon_bars
    ):
        return OutcomeResult(
            ResultStatus.INSUFFICIENT_DATA, reason="complete forward window required"
        )
    reference, future = source.window.reference_price, source.bars[-1].close
    exponent = min(cast(int, reference.as_tuple().exponent), cast(int, future.as_tuple().exponent))
    precision = max(28, max(reference.adjusted(), future.adjusted()) - exponent + 2)
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        value = future - reference
    if percent:
        if reference == 0:
            return OutcomeResult(ResultStatus.NOT_APPLICABLE, reason="reference price is zero")
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            value = value / reference
    if directional:
        direction = _direction(source)
        if direction is None:
            return OutcomeResult(
                ResultStatus.NOT_APPLICABLE,
                reason="source has no available captured direction",
            )
        if direction == "DOWN":
            value = value.copy_negate()
    return OutcomeResult(ResultStatus.AVAILABLE, value)


def forward_return(source: OutcomeInput) -> OutcomeResult:
    return _calculate(source, FORWARD_RETURN, percent=False)


def forward_return_percent(source: OutcomeInput) -> OutcomeResult:
    return _calculate(source, FORWARD_RETURN_PERCENT, percent=True)


def default_forward_return_selections(
    *, percent: bool = False, directional: bool = False
) -> tuple[OutcomeSelection, ...]:
    definition = FORWARD_RETURN_PERCENT if percent else FORWARD_RETURN
    return tuple(
        OutcomeSelection(
            outcome_id=definition.outcome_id,
            outcome_version=definition.outcome_version,
            parameters=(
                ConfigParameter(name="horizon", value=horizon),
                ConfigParameter(name="directional", value=directional),
            ),
        )
        for horizon in DEFAULT_HORIZONS
    )
