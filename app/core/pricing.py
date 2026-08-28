"""Pinned pricing constants and the cost calculator.

Every money value in this system is an ``int`` of **micro-cents**.

    1 US cent = 1_000_000 micro-cents
    1 US dollar = 100_000_000 micro-cents

Cents alone are too coarse: a single token costs a small fraction of a cent, so
rounding each event to whole cents would either leak revenue or overcharge.
See docs/REFERENCES.md rules M1-M4 and T1-T5.

The three rules the brief calls out as the hard part, encoded here:

1. Cached input tokens are cheaper, priced with their OWN constant, and are not
   also billed as fresh input. The two counts are disjoint.
2. Reasoning tokens are billed at the OUTPUT rate -- ``PRICE_REASONING_PER_MTOK``
   is literally the same object as ``PRICE_OUTPUT_PER_MTOK`` so the two can never
   drift apart.
3. Token categories are never summed before pricing.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- unit conversions -------------------------------------------------------

MICRO_CENTS_PER_CENT = 1_000_000
MICRO_CENTS_PER_DOLLAR = 100 * MICRO_CENTS_PER_CENT
TOKENS_PER_MILLION = 1_000_000

# --- pinned prices ----------------------------------------------------------
# Shape follows the Gemini API pricing reference cited in docs/REFERENCES.md #6:
# fresh input, context-cached input and output are three distinct rates, and
# thinking/reasoning tokens are billed at the output rate.

#: $0.00200 per billable API call.
PRICE_API_CALL: int = 2_000_000

#: $0.75 per 1M fresh input tokens.
PRICE_INPUT_PER_MTOK: int = 75_000_000

#: $0.075 per 1M cached input tokens -- 10x cheaper than fresh input.
PRICE_CACHED_INPUT_PER_MTOK: int = 7_500_000

#: $3.75 per 1M output tokens.
PRICE_OUTPUT_PER_MTOK: int = 375_000_000

#: Reasoning tokens are billed AS output tokens. Same constant on purpose --
#: binding by identity means no future edit can price them differently by
#: accident. Asserted in tests.
PRICE_REASONING_PER_MTOK: int = PRICE_OUTPUT_PER_MTOK

#: Every plan's monthly allowance. Free is fixed by the brief; Pro is our choice.
PLAN_QUOTAS: dict[str, dict[str, int]] = {
    "free": {"api_calls": 1_000, "tokens": 100_000},
    "pro": {"api_calls": 50_000, "tokens": 5_000_000},
}


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """The four token counters, kept separate all the way to the price.

    ``input_tokens`` and ``cached_input_tokens`` are *disjoint* counts: a token
    served from cache is counted here and NOT also in ``input_tokens``.
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Sum of all four counters -- used for QUOTA counting only, never for pricing.

        A token consumed is a token consumed as far as the plan allowance goes,
        but the money math must keep the categories apart (rule T5).
        """
        return (
            self.input_tokens
            + self.cached_input_tokens
            + self.output_tokens
            + self.reasoning_tokens
        )


def price_tokens(usage: TokenUsage) -> int:
    """Cost of a token bundle, in integer micro-cents.

    Integer arithmetic throughout, with the division deferred to the end of each
    term so intermediate rounding cannot cascade (rule M3). Floor division means
    a fraction of a micro-cent is never rounded up against the customer (M4).
    """
    return (
        (usage.input_tokens * PRICE_INPUT_PER_MTOK) // TOKENS_PER_MILLION
        + (usage.cached_input_tokens * PRICE_CACHED_INPUT_PER_MTOK) // TOKENS_PER_MILLION
        # Output and reasoning share one rate, so they share one term.
        + ((usage.output_tokens + usage.reasoning_tokens) * PRICE_OUTPUT_PER_MTOK)
        // TOKENS_PER_MILLION
    )


def price_api_calls(api_calls: int) -> int:
    """Cost of N billable API calls, in integer micro-cents."""
    return api_calls * PRICE_API_CALL


def price_event(api_calls: int, usage: TokenUsage) -> int:
    """Total cost of one usage event, in integer micro-cents."""
    return price_api_calls(api_calls) + price_tokens(usage)
