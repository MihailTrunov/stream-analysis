"""Local live-feed subscriptions; provider credentials never cross this boundary."""

import os
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from market_analysis.application.browser_replay import _store
from market_analysis.application.history_ranges import HistoryFeed
from market_analysis.application.live_analysis import LiveAnalysisRequest, live_analysis
from market_analysis.application.live_ingress import live_ingress
from market_analysis.application.live_inputs import read_live_input
from market_analysis.config.oanda_uk_calendars import OANDA_UK_LIVE_PROFILES
from market_analysis.domain import Timeframe

router = APIRouter(prefix="/live", tags=["live"])


def configured_feed(instrument_id: Literal["US30", "DAX"]) -> HistoryFeed:
    account = os.getenv("OANDA_ACCOUNT") or ""
    if (not account or not (os.getenv("OANDA_KEY") or os.getenv("OANDA_TOKEN"))
        or (os.getenv("OANDA_ENV") or "live").lower() != "live"
        or (os.getenv("OANDA_REGION") or "UK").upper() != "UK"):
        raise HTTPException(status_code=503, detail="Live provider configuration is unavailable.")
    profile = next(item for item in OANDA_UK_LIVE_PROFILES.values()
                   if item.instrument_id == instrument_id)
    return HistoryFeed(instrument_id=instrument_id, provider_symbol=profile.provider_symbol,
                       calendar_version=profile.version,
                       account_fingerprint=sha256(account.encode()).hexdigest())


class SubscribeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subscription_id: UUID
    instrument_id: Literal["US30", "DAX"]
    start: datetime | None = None


class LiveSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: datetime
    end: datetime


@router.post("/subscriptions", status_code=201)
def subscribe(request: SubscribeRequest) -> dict[str, object]:
    feed = configured_feed(request.instrument_id)
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
    live_analysis.disconnect(str(subscription_id))
    live_ingress.unsubscribe(str(subscription_id))
    return {"status": "disconnected"}


@router.post("/analysis/{instrument_id}", status_code=201)
def connect_analysis(
    instrument_id: Literal["US30", "DAX"], request: LiveAnalysisRequest,
) -> dict[str, object]:
    try:
        return live_analysis.connect(request, configured_feed(instrument_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/prepare/{instrument_id}")
def prepare_analysis(
    instrument_id: Literal["US30", "DAX"], request: LiveAnalysisRequest,
) -> dict[str, object]:
    feed = configured_feed(instrument_id)
    try:
        return live_analysis.prepare(request, feed)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/analysis/{subscription_id}/view")
def analysis_view(
    subscription_id: UUID, timeframe: Timeframe = Timeframe.M1, end: datetime | None = None,
) -> dict[str, object]:
    try:
        return live_analysis.view(str(subscription_id), timeframe, end)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/inputs/{input_id}")
def input_manifest(input_id: str) -> dict[str, object]:
    engine = live_analysis._analysis.engine()
    try:
        with engine.connect() as connection:
            return read_live_input(connection, _store(), input_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        engine.dispose()


@router.post("/analysis/{subscription_id}/history", status_code=201)
def historical_snapshot(subscription_id: UUID, request: LiveSnapshotRequest) -> dict[str, object]:
    try:
        return live_analysis.snapshot(str(subscription_id), request.start, request.end)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
