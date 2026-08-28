"""Pydantic schemas -- validation at the boundary (requirement S2).

Bad input becomes a clean 4xx here, never a 500 deeper in. Token counts are
constrained to non-negative integers, so a negative "refund" cannot be smuggled
in through the metering endpoint.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SimulatedTokens(BaseModel):
    """The four token categories, metered separately.

    ``input`` and ``cached_input`` are DISJOINT counts: a token served from cache
    belongs in ``cached_input`` and must not also be reported as ``input``.
    """

    model_config = ConfigDict(extra="forbid")

    input: int = Field(default=0, ge=0, le=10_000_000)
    cached_input: int = Field(default=0, ge=0, le=10_000_000)
    output: int = Field(default=0, ge=0, le=10_000_000)
    #: Hidden "thinking" tokens. Billed at the OUTPUT rate, never free.
    reasoning: int = Field(default=0, ge=0, le=10_000_000)


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=10_000)
    simulated_tokens: SimulatedTokens = Field(default_factory=SimulatedTokens)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: str = Field(default="pro", pattern="^(pro)$")


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
