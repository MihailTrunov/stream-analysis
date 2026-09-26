from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType


class DomainValidationError(ValueError):
    """Raised when a canonical domain value violates an invariant."""


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"


@dataclass(frozen=True, slots=True)
class ProviderSymbolMapping:
    provider: str
    symbol: str
    environment: str | None = None

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise DomainValidationError("provider must be non-empty")
        if not self.symbol.strip():
            raise DomainValidationError("provider symbol must be non-empty")
        if self.environment is not None and not self.environment.strip():
            raise DomainValidationError("environment must be non-empty when provided")


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    display_name: str
    calendar_id: str
    price_precision: int
    point_size: Decimal
    provider_symbols: tuple[ProviderSymbolMapping, ...] = ()

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise DomainValidationError("instrument_id must be non-empty")
        if not self.display_name.strip():
            raise DomainValidationError("display_name must be non-empty")
        if not self.calendar_id.strip():
            raise DomainValidationError("calendar_id must be non-empty")
        if self.price_precision < 0:
            raise DomainValidationError("price_precision must be >= 0")
        point_size = _decimal(self.point_size, "point_size")
        if point_size <= 0:
            raise DomainValidationError("point_size must be > 0")
        object.__setattr__(self, "point_size", point_size)

        provider_symbols = tuple(self.provider_symbols)
        if any(not isinstance(item, ProviderSymbolMapping) for item in provider_symbols):
            raise DomainValidationError(
                "provider_symbols must contain ProviderSymbolMapping values"
            )
        keys = [
            (item.provider.casefold(), (item.environment or "").casefold())
            for item in provider_symbols
        ]
        if len(keys) != len(set(keys)):
            raise DomainValidationError(
                "provider/environment mappings must be unique"
            )
        object.__setattr__(
            self,
            "provider_symbols",
            tuple(
                sorted(
                    provider_symbols,
                    key=lambda value: (
                        value.provider.casefold(),
                        (value.environment or "").casefold(),
                    ),
                )
            ),
        )

    def provider_symbol(
        self,
        provider: str,
        *,
        environment: str | None = None,
    ) -> str:
        key = (provider.casefold(), (environment or "").casefold())
        for item in self.provider_symbols:
            item_key = (
                item.provider.casefold(),
                (item.environment or "").casefold(),
            )
            if item_key == key:
                return item.symbol
        raise KeyError(
            f"no provider mapping for {provider!r} environment={environment!r}"
        )

    def to_canonical_dict(self) -> Mapping[str, object]:
        payload: dict[str, object] = {
            "instrument_id": self.instrument_id,
            "display_name": self.display_name,
            "calendar_id": self.calendar_id,
            "price_precision": self.price_precision,
            "point_size": _decimal_text(self.point_size),
            "provider_symbols": [
                {
                    "provider": item.provider,
                    "symbol": item.symbol,
                    "environment": item.environment,
                }
                for item in self.provider_symbols
            ],
        }
        return MappingProxyType(payload)


@dataclass(frozen=True, slots=True)
class Bar:
    instrument_id: str
    timeframe: Timeframe
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    source_id: str = "canonical"
    is_complete: bool = True
    quality_flags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.instrument_id.strip():
            raise DomainValidationError("instrument_id must be non-empty")
        if not isinstance(self.timeframe, Timeframe):
            try:
                object.__setattr__(self, "timeframe", Timeframe(self.timeframe))
            except ValueError as exc:
                raise DomainValidationError(
                    f"unsupported timeframe: {self.timeframe!r}"
                ) from exc
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise DomainValidationError("timestamp must be timezone-aware")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))

        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, _decimal(getattr(self, name), name))
        if self.volume is not None:
            volume = _decimal(self.volume, "volume")
            if volume < 0:
                raise DomainValidationError("volume must be >= 0")
            object.__setattr__(self, "volume", volume)

        if self.high < max(self.open, self.close):
            raise DomainValidationError("high must be >= open and close")
        if self.low > min(self.open, self.close):
            raise DomainValidationError("low must be <= open and close")
        if self.high < self.low:
            raise DomainValidationError("high must be >= low")
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise DomainValidationError("source_id must be non-empty")
        if not isinstance(self.is_complete, bool):
            raise DomainValidationError("is_complete must be a boolean")
        quality_flags = frozenset(self.quality_flags)
        if any(
            not isinstance(flag, str) or not flag.strip()
            for flag in quality_flags
        ):
            raise DomainValidationError("quality flags must be non-empty strings")
        object.__setattr__(self, "quality_flags", quality_flags)

    @property
    def identity(self) -> tuple[str, Timeframe, datetime]:
        return (self.instrument_id, self.timeframe, self.timestamp)

    def to_canonical_dict(self) -> Mapping[str, object]:
        payload: dict[str, object] = {
            "instrument_id": self.instrument_id,
            "timeframe": self.timeframe.value,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "open": _decimal_text(self.open),
            "high": _decimal_text(self.high),
            "low": _decimal_text(self.low),
            "close": _decimal_text(self.close),
            "volume": (
                None if self.volume is None else _decimal_text(self.volume)
            ),
            "source_id": self.source_id,
            "is_complete": self.is_complete,
            "quality_flags": sorted(self.quality_flags),
        }
        return MappingProxyType(payload)

    @classmethod
    def from_canonical_dict(cls, payload: Mapping[str, object]) -> Bar:
        timestamp = payload.get("timestamp")
        if not isinstance(timestamp, str):
            raise DomainValidationError("timestamp must be an ISO-8601 string")
        try:
            parsed_timestamp = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise DomainValidationError(
                "timestamp must be valid ISO-8601"
            ) from exc

        flags = payload.get("quality_flags", ())
        if not isinstance(flags, list | tuple | set | frozenset):
            raise DomainValidationError("quality_flags must be a sequence")
        if any(not isinstance(value, str) for value in flags):
            raise DomainValidationError("quality_flags must contain strings")
        timeframe_value = _required_str(payload, "timeframe")
        try:
            timeframe = Timeframe(timeframe_value)
        except ValueError as exc:
            raise DomainValidationError(
                f"unsupported timeframe: {timeframe_value!r}"
            ) from exc

        return cls(
            instrument_id=_required_str(payload, "instrument_id"),
            timeframe=timeframe,
            timestamp=parsed_timestamp,
            open=_required_decimal(payload, "open"),
            high=_required_decimal(payload, "high"),
            low=_required_decimal(payload, "low"),
            close=_required_decimal(payload, "close"),
            volume=(
                None
                if payload.get("volume") is None
                else _required_decimal(payload, "volume")
            ),
            source_id=_required_str(payload, "source_id"),
            is_complete=_required_bool(payload, "is_complete"),
            quality_flags=frozenset(flags),
        )


def _decimal(value: object, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise DomainValidationError(f"{field_name} must be numeric")
    try:
        decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DomainValidationError(
            f"{field_name} must be a finite decimal"
        ) from exc
    if not decimal.is_finite():
        raise DomainValidationError(f"{field_name} must be a finite decimal")
    return decimal


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise DomainValidationError(f"{key} must be a string")
    return value


def _required_bool(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise DomainValidationError(f"{key} must be a boolean")
    return value


def _required_decimal(payload: Mapping[str, object], key: str) -> Decimal:
    if key not in payload:
        raise DomainValidationError(f"{key} is required")
    return _decimal(payload[key], key)
