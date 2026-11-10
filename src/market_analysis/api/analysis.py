"""Workspace orchestration transport; legacy replay endpoints stay compatible."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from market_analysis.application.analysis_sessions import (
    AnalysisRequest,
    analysis_sessions,
    history_candles,
)
from market_analysis.application.chart_tasks import chart_tasks
from market_analysis.domain import Timeframe

router = APIRouter(prefix="/analysis", tags=["analysis"])


def checked(operation: Any, *args: Any) -> Any:
    try:
        return operation(*args)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("", status_code=201)
def launch(request: AnalysisRequest, prefer: str | None = Header(default=None)) -> Any:
    if prefer == 'respond-async':
        return JSONResponse(status_code=202, content=checked(chart_tasks.submit,
            lambda task: analysis_sessions.launch(request, checkpoint=task.checkpoint),
            lambda result: analysis_sessions.command(result['run_id'], 'stop')))
    return checked(analysis_sessions.launch, request)


@router.get('/tasks/{task_id}')
def task_state(task_id: UUID) -> Any:
    return checked(chart_tasks.state, str(task_id))


@router.delete('/tasks/{task_id}')
def cancel_task(task_id: UUID) -> Any:
    chart_tasks.cancel(str(task_id))
    return {'status': 'cancelled'}


@router.get("/active")
def active() -> Any:
    return {"sessions": analysis_sessions.active()}


@router.get("/history/{revision_id}")
def history(
    revision_id: str,
    instrument_id: str,
    timeframe: Timeframe,
    start: datetime,
    end: datetime,
    limit: int = 500,
) -> Any:
    return checked(history_candles, revision_id, instrument_id, timeframe, start, end, limit)


@router.get("/{session_id}")
def state(session_id: str) -> Any:
    return checked(analysis_sessions.state, session_id)


@router.get("/{session_id}/view")
def view(
    session_id: str,
    start: datetime,
    end: datetime,
    timeframe: Timeframe = Timeframe.M1,
    limit: int = 500,
) -> Any:
    return checked(analysis_sessions.view, session_id, timeframe, start, end, limit)


@router.post("/{session_id}/{action}")
def command(session_id: str, action: str, timeframe: Timeframe | None = None) -> Any:
    return checked(analysis_sessions.command, session_id, action, timeframe)
