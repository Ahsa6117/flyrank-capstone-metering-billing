"""Billable and read endpoints. HTTP only -- no SQL, no business rules."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import IdempotencyKeyDep, SessionDep, TenantDep
from app.api.schemas import GenerateRequest
from app.core.pricing import TokenUsage
from app.services.metering import MeterService
from app.services.usage_reporting import UsageReportingService

router = APIRouter(prefix="/v1", tags=["metering"])


@router.post("/generate", summary="The billable action: meter, enforce, price")
def generate(
    payload: GenerateRequest,
    tenant: TenantDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
) -> dict:
    """Simulate an AI generation and bill for it, exactly once.

    Tokens are *simulated* -- the brief is explicit that no model call is needed,
    because the subject here is metering numbers, not generating text.
    """
    tokens = TokenUsage(
        input_tokens=payload.simulated_tokens.input,
        cached_input_tokens=payload.simulated_tokens.cached_input,
        output_tokens=payload.simulated_tokens.output,
        reasoning_tokens=payload.simulated_tokens.reasoning,
    )

    result = MeterService(session).record(
        tenant,
        event_type="generate",
        api_calls=1,
        tokens=tokens,
        idempotency_key=idempotency_key,
        # Fingerprint the validated payload, so whitespace and key order in the
        # raw body cannot make a genuine retry look like a different request.
        request_payload=payload.model_dump(mode="json"),
    )

    # Advertise the replay explicitly, so a caller inspecting headers can tell a
    # deduplicated retry from a fresh charge.
    response.headers["Idempotent-Replay"] = "true" if result.replayed else "false"
    response.status_code = result.status_code
    return result.body


@router.get("/usage", summary="Monthly rollup: used, limit, cost")
def usage(tenant: TenantDep, session: SessionDep) -> dict:
    """Aggregate this tenant's events for the current UTC month.

    No Idempotency-Key: GET is idempotent by definition (rule I4).
    """
    return UsageReportingService(session).report(tenant)
