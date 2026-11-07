"""Frozen evaluation submission and durable reads; scheduling is a separate use case."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, create_engine

from market_analysis.application.evaluation_runs import (
    create_evaluation_run,
    evaluation_events,
    evaluation_state,
)
from market_analysis.domain.evaluation import EvaluationNotFound, EvaluationRequest
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.transactions import atomic_transaction

router = APIRouter(prefix="/evaluation-runs", tags=["evaluation"])


def database() -> Iterator[Connection]:
    url = os.getenv("STREAM_ANALYSIS_DATABASE_URL")
    if not url:
        raise HTTPException(status_code=503, detail="database is not configured")
    engine = create_engine(url)
    try:
        with atomic_transaction(engine) as connection:
            yield connection
    finally:
        engine.dispose()


@router.post("", status_code=201)
def create(request: EvaluationRequest, connection: Annotated[Connection, Depends(database)]) -> Any:
    try:
        record = create_evaluation_run(
            connection,
            request,
            DatasetStore(Path(os.getenv("STREAM_ANALYSIS_DATA_ROOT", "./data"))),
            build_id=os.getenv("STREAM_ANALYSIS_BUILD_ID", "development"),
            created_at=datetime.now(UTC),
        )
        return evaluation_state(connection, str(record.inputs.evaluation_id))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/{evaluation_id}")
def state(evaluation_id: UUID, connection: Annotated[Connection, Depends(database)]) -> Any:
    try:
        return evaluation_state(connection, str(evaluation_id))
    except EvaluationNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/{evaluation_id}/events")
def events(
    evaluation_id: UUID,
    connection: Annotated[Connection, Depends(database)],
    after: int = -1,
    limit: int = 100,
    eligible_only: bool = True,
) -> Any:
    try:
        return evaluation_events(
            connection, str(evaluation_id), after=after, limit=limit, eligible_only=eligible_only
        )
    except EvaluationNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/{evaluation_id}/segments")
def segments(evaluation_id: UUID, connection: Annotated[Connection, Depends(database)]) -> Any:
    from market_analysis.application.segmentation import evaluation_segments

    try:
        return evaluation_segments(connection, str(evaluation_id))
    except EvaluationNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
