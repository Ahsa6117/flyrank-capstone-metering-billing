"""Operator endpoint: run the background job on demand.

Exists so an evaluator can prove the job works in one call instead of waiting
15 minutes for the scheduler. Token-protected so it is not publicly triggerable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header

from app.api.errors import error_response
from app.core.config import get_settings
from app.jobs.rollup_and_alerts import run_job

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/jobs/run", summary="Trigger the rollup + alerts job now")
def trigger_job(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
):
    settings = get_settings()
    if x_internal_token != settings.internal_job_token:
        return error_response(
            403, "forbidden", "A valid X-Internal-Token header is required."
        )
    return run_job()
