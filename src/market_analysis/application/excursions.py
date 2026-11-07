"""Detection-close price excursions with immutable direction and ATR bases."""

from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from functools import partial
from typing import Literal, cast

from market_analysis.application.outcome_basis import captured_direction, exact_difference
from market_analysis.application.outcome_evaluation import (
    OutcomeAlgorithm,
    OutcomeInput,
    OutcomeResult,
)
from market_analysis.config import OutcomeSelection
from market_analysis.domain.outcomes import (
    OutcomeDefinition,
    OutcomeError,
    OutputType,
    ResultStatus,
)
from market_analysis.patterns import ParameterSpec, ParameterType

Kind = Literal["MFE", "MAE", "UP_EXCURSION", "DOWN_EXCURSION"]
KINDS: tuple[Kind, ...] = ("MFE", "MAE", "UP_EXCURSION", "DOWN_EXCURSION")
HORIZON = ParameterSpec(
    "horizon", ParameterType.INTEGER, 5, minimum=Decimal(1), maximum=Decimal(100000)
)
ATR_INSTANCE = ParameterSpec("atr_instance_id", ParameterType.STRING, "atr")


def _definition(kind: Kind, normalized: bool) -> OutcomeDefinition:
    return OutcomeDefinition(
        outcome_id=f"{kind}_ATR" if normalized else kind,
        outcome_version="1",
        output_type=OutputType.NUMERIC,
        parameters=(HORIZON, ATR_INSTANCE) if normalized else (HORIZON,),
        units="atr_multiple" if normalized else "points",
        normalization="CAPTURED_ATR_DECIMAL28_HALF_EVEN" if normalized else "NONE",
        direction_convention=(
            "CAPTURED_MVP_DIRECTION" if kind in ("MFE", "MAE") else "INSTRUMENT_PRICE"
        ),
    )


DEFINITIONS = tuple(_definition(kind, normalized) for kind in KINDS for normalized in (False, True))
_BY_ID = {definition.outcome_id: definition for definition in DEFINITIONS}


def _captured_atr(source: OutcomeInput, selection: OutcomeSelection) -> Decimal | None:
    instance_id = next(
        item.value for item in selection.parameters if item.name == "atr_instance_id"
    )
    components = source.context.get("components")
    if not isinstance(components, Mapping):
        return None
    component = components.get(instance_id)
    if (
        not isinstance(component, Mapping)
        or component.get("component_id") != "atr"
        or component.get("instance_id") != instance_id
        or component.get("value_ready") is not True
    ):
        return None
    raw = component.get("atr")
    if not isinstance(raw, str | Decimal | int) or isinstance(raw, bool):
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def excursion(source: OutcomeInput, *, kind: Kind, normalized: bool = False) -> OutcomeResult:
    definition = _BY_ID[f"{kind}_ATR" if normalized else kind]
    if source.configuration is None:
        raise OutcomeError("excursion requires its resolved outcome configuration")
    selection = definition.resolve(source.configuration)
    if (
        len(source.bars) != source.window.horizon_bars
        or source.window.observed_bars != source.window.horizon_bars
    ):
        return OutcomeResult(
            ResultStatus.INSUFFICIENT_DATA, reason="complete forward window required"
        )
    reference = source.window.reference_price
    up = exact_difference(max(bar.high for bar in source.bars), reference)
    down = exact_difference(reference, min(bar.low for bar in source.bars))
    if kind in ("MFE", "MAE"):
        direction = captured_direction(source)
        if direction is None:
            return OutcomeResult(
                ResultStatus.NOT_APPLICABLE, reason="source has no available captured direction"
            )
        value = (
            (up if direction == "UP" else down)
            if kind == "MFE"
            else (down.copy_negate() if direction == "UP" else up.copy_negate())
        )
    else:
        value = up if kind == "UP_EXCURSION" else down
    if normalized:
        atr = _captured_atr(source, selection)
        if atr is None:
            return OutcomeResult(
                ResultStatus.NOT_APPLICABLE,
                reason="source has no ready positive captured ATR for selected instance",
            )
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            value = value / atr
    return OutcomeResult(ResultStatus.AVAILABLE, value)


ALGORITHMS: Mapping[tuple[str, str], OutcomeAlgorithm] = {
    definition.identity: cast(
        OutcomeAlgorithm,
        partial(excursion, kind=kind, normalized=normalized),
    )
    for kind in KINDS
    for normalized in (False, True)
    for definition in (_BY_ID[f"{kind}_ATR" if normalized else kind],)
}
