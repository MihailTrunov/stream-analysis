"""Durable evaluation queue transport; workspace Runs presentation is separate."""

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection

from market_analysis.api.evaluations import database
from market_analysis.application.evaluation_scheduler import (
    cancel_job,
    job_state,
    queue_state,
    reorder_waiting,
    set_automatic,
    submit_job,
)
from market_analysis.domain.evaluation import EvaluationRequest
from market_analysis.persistence.dataset_store import DatasetStore
from market_analysis.persistence.evaluation_jobs import EvaluationJobNotFound

router = APIRouter(tags=["evaluation-queue"])
Database = Annotated[Connection, Depends(database)]


class QueueOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_ids: list[UUID] = Field(max_length=1000)


def perform(operation: Callable[[], Any], *, status: int = 409) -> Any:
    try:
        return operation()
    except EvaluationJobNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status, detail=str(error)) from error


@router.post("/evaluation-jobs", status_code=201)
def submit(request: EvaluationRequest, connection: Database) -> Any:
    def operation() -> Any:
        job = submit_job(
            connection,
            request,
            DatasetStore(Path(os.getenv("STREAM_ANALYSIS_DATA_ROOT", "./data"))),
            build_id=os.getenv("STREAM_ANALYSIS_BUILD_ID", "development"),
            at=datetime.now(UTC),
        )
        return job_state(connection, job.job_id)

    return perform(operation, status=422)


@router.get("/evaluation-jobs/{job_id}")
def job(job_id: UUID, connection: Database) -> Any:
    return perform(lambda: job_state(connection, str(job_id)))


@router.post("/evaluation-jobs/{job_id}/cancel")
def cancel(job_id: UUID, connection: Database) -> Any:
    def operation() -> Any:
        cancel_job(connection, str(job_id), datetime.now(UTC))
        return job_state(connection, str(job_id))

    return perform(operation)


@router.get("/evaluation-queue")
def state(connection: Database, history_limit: int = 100) -> Any:
    return perform(lambda: queue_state(connection, history_limit=history_limit), status=422)


@router.post("/evaluation-queue/start")
def start(connection: Database) -> Any:
    def operation() -> Any:
        set_automatic(connection, True)
        return queue_state(connection)

    return perform(operation)


@router.post("/evaluation-queue/pause")
def pause(connection: Database) -> Any:
    def operation() -> Any:
        set_automatic(connection, False)
        return queue_state(connection)

    return perform(operation)


@router.put("/evaluation-queue/order")
def order(request: QueueOrder, connection: Database) -> Any:
    def operation() -> Any:
        reorder_waiting(connection, [str(identity) for identity in request.job_ids])
        return queue_state(connection)

    return perform(operation)
