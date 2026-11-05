"""Local live-feed subscriptions; provider credentials never cross this boundary."""

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from market_analysis.application.history_ranges import HistoryFeed
from market_analysis.application.live_ingress import live_ingress
from market_analysis.config.oanda_uk_calendars import OANDA_UK_LIVE_PROFILES

router = APIRouter(prefix="/live", tags=["live"])


class SubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription_id: UUID
    instrument_id: Literal["US30", "DAX"]
    start: datetime | None = None


@router.post("/subscriptions", status_code=201)
def subscribe(request: SubscribeRequest) -> dict[str, object]:
    account = os.getenv("OANDA_ACCOUNT") or ""
    if (not account or not (os.getenv("OANDA_KEY") or os.getenv("OANDA_TOKEN"))
        or (os.getenv("OANDA_ENV") or "live").lower() != "live"
        or (os.getenv("OANDA_REGION") or "UK").upper() != "UK"):
        raise HTTPException(status_code=503, detail="Live provider configuration is unavailable.")
    profile = next(item for item in OANDA_UK_LIVE_PROFILES.values()
                   if item.instrument_id == request.instrument_id)
    feed = HistoryFeed(instrument_id=request.instrument_id, provider_symbol=profile.provider_symbol,
                       calendar_version=profile.version,
                       account_fingerprint=sha256(account.encode()).hexdigest())
    start = request.start or (
        datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
    )
    try:
        return live_ingress.subscribe(str(request.subscription_id), feed, start)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/subscriptions/{subscription_id}")
def state(subscription_id: UUID) -> dict[str, object]:
    try:
        return live_ingress.state(str(subscription_id))
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/subscriptions/{subscription_id}/bars")
def bars(
    subscription_id: UUID, start: datetime, end: datetime, limit: int = 500,
) -> dict[str, object]:
    try:
        return live_ingress.bars(str(subscription_id), start, end, limit)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/subscriptions/{subscription_id}")
def disconnect(subscription_id: UUID) -> dict[str, str]:
    live_ingress.unsubscribe(str(subscription_id))
    return {"status": "disconnected"}
