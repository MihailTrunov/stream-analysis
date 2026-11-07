"""Exact first-touch classification and observable offsets over frozen future windows."""

from collections.abc import Mapping
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from functools import partial
from typing import Literal, cast

from market_analysis.application.outcome_basis import (
    captured_atr,
    captured_direction,
    exact_difference,
)
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

Kind = Literal["CATEGORY", "BARS", "SECONDS"]
PARAMETERS = (
    ParameterSpec("horizon", ParameterType.INTEGER, 5, minimum=Decimal(1), maximum=Decimal(100000)),
    ParameterSpec(
        "basis",
        ParameterType.STRING,
        "EVENT_DIRECTION",
        supported_values=("EVENT_DIRECTION", "UP_DOWN"),
    ),
    ParameterSpec("positive_threshold", ParameterType.DECIMAL, Decimal(1), minimum=Decimal(0)),
    ParameterSpec("negative_threshold", ParameterType.DECIMAL, Decimal(1), minimum=Decimal(0)),
    ParameterSpec(
        "positive_units", ParameterType.STRING, "POINTS", supported_values=("POINTS", "ATR")
    ),
    ParameterSpec(
        "negative_units", ParameterType.STRING, "POINTS", supported_values=("POINTS", "ATR")
    ),
    ParameterSpec("atr_instance_id", ParameterType.STRING, "atr"),
)
SIDE = ParameterSpec(
    "side",
    ParameterType.STRING,
    "POSITIVE",
    supported_values=("POSITIVE", "NEGATIVE"),
    description="For UP_DOWN basis, POSITIVE means UP and NEGATIVE means DOWN.",
)


def _definition(kind: Kind) -> OutcomeDefinition:
    return OutcomeDefinition(
        outcome_id="TIME_TO_THRESHOLD" if kind == "CATEGORY" else f"TIME_TO_THRESHOLD_{kind}",
        outcome_version="1",
        output_type=OutputType.CATEGORICAL if kind == "CATEGORY" else OutputType.NUMERIC,
        parameters=PARAMETERS if kind == "CATEGORY" else (*PARAMETERS, SIDE),
        units={"CATEGORY": "first_side", "BARS": "m1_bars", "SECONDS": "seconds"}[kind],
        normalization="POINTS_OR_EXACT_CAPTURED_ATR_PRODUCT",
        direction_convention="CAPTURED_MVP_DIRECTION_OR_EXPLICIT_UP_DOWN",
        categories=("POSITIVE_FIRST", "NEGATIVE_FIRST", "UP_FIRST", "DOWN_FIRST", "NOT_REACHED")
        if kind == "CATEGORY"
        else (),
    )


KINDS: tuple[Kind, ...] = ("CATEGORY", "BARS", "SECONDS")
DEFINITIONS = tuple(_definition(kind) for kind in KINDS)
_BY_KIND = dict(zip(KINDS, DEFINITIONS, strict=True))


def _distance(source: OutcomeInput, parameters: Mapping[str, object], side: str) -> Decimal | None:
    value = cast(Decimal, parameters[f"{side}_threshold"])
    if parameters[f"{side}_units"] == "POINTS":
        return value
    atr = captured_atr(source, parameters["atr_instance_id"])
    if atr is None:
        return None
    precision = max(28, len(value.as_tuple().digits) + len(atr.as_tuple().digits))
    with localcontext(Context(prec=precision, rounding=ROUND_HALF_EVEN)):
        return value * atr


def time_to_threshold(source: OutcomeInput, *, kind: Kind = "CATEGORY") -> OutcomeResult:
    if source.configuration is None:
        raise OutcomeError("time to threshold requires its resolved configuration")
    selection: OutcomeSelection = _BY_KIND[kind].resolve(source.configuration)
    parameters = {item.name: item.value for item in selection.parameters}
    if (
        len(source.bars) != source.window.horizon_bars
        or source.window.observed_bars != source.window.horizon_bars
    ):
        return OutcomeResult(
            ResultStatus.INSUFFICIENT_DATA, reason="complete forward window required"
        )
    direction = "UP" if parameters["basis"] == "UP_DOWN" else captured_direction(source)
    if direction is None:
        return OutcomeResult(
            ResultStatus.NOT_APPLICABLE,
            reason=(
                "source has no available captured direction; choose UP_DOWN for explicit price axes"
            ),
        )
    positive, negative = (
        _distance(source, parameters, "positive"),
        _distance(source, parameters, "negative"),
    )
    if (kind == "CATEGORY" and (positive is None or negative is None)) or (
        kind != "CATEGORY" and (positive if parameters["side"] == "POSITIVE" else negative) is None
    ):
        return OutcomeResult(
            ResultStatus.NOT_APPLICABLE,
            reason="source has no ready positive captured ATR for selected instance",
        )
    first_positive: int | None = None
    first_negative: int | None = None
    reference = source.window.reference_price
    for index, bar in enumerate(source.bars):
        up = exact_difference(bar.high, reference)
        down = exact_difference(reference, bar.low)
        if (
            positive is not None
            and first_positive is None
            and (up if direction == "UP" else down) >= positive
        ):
            first_positive = index
        if (
            negative is not None
            and first_negative is None
            and (down if direction == "UP" else up) >= negative
        ):
            first_negative = index
        if first_positive is not None and first_negative is not None:
            break
    if kind != "CATEGORY":
        hit_index = first_positive if parameters["side"] == "POSITIVE" else first_negative
        if hit_index is None:
            return OutcomeResult(
                ResultStatus.NOT_APPLICABLE,
                reason="NOT_REACHED: selected threshold was not touched in the complete window",
            )
        if kind == "BARS":
            value = Decimal(hit_index + 1)
        else:
            elapsed = (
                source.bars[hit_index].timestamp
                + timedelta(minutes=1)
                - source.window.reference_time
            )
            value = Decimal(elapsed.days * 86400 + elapsed.seconds)
        return OutcomeResult(ResultStatus.AVAILABLE, value)
    if first_positive is None and first_negative is None:
        return OutcomeResult(ResultStatus.AVAILABLE, "NOT_REACHED")
    if first_positive is not None and first_positive == first_negative:
        return OutcomeResult(
            ResultStatus.AMBIGUOUS,
            reason=(
                "SAME_BAR_AMBIGUOUS: both thresholds first touched in the same OHLC candle; "
                "intrabar order is unknown"
            ),
        )
    positive_first = first_positive is not None and (
        first_negative is None or first_positive < first_negative
    )
    label = (
        ("UP" if positive_first else "DOWN")
        if parameters["basis"] == "UP_DOWN"
        else ("POSITIVE" if positive_first else "NEGATIVE")
    )
    return OutcomeResult(ResultStatus.AVAILABLE, f"{label}_FIRST")


ALGORITHMS: Mapping[tuple[str, str], OutcomeAlgorithm] = {
    _BY_KIND[kind].identity: cast(OutcomeAlgorithm, partial(time_to_threshold, kind=kind))
    for kind in KINDS
}
