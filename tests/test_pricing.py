"""Money math — PROBE 5.

The three token rules the brief calls the hard part, each asserted directly.
"""

from __future__ import annotations

from app.core import pricing
from app.core.money import to_usd_string
from app.core.pricing import (
    PRICE_API_CALL,
    PRICE_CACHED_INPUT_PER_MTOK,
    PRICE_INPUT_PER_MTOK,
    PRICE_OUTPUT_PER_MTOK,
    PRICE_REASONING_PER_MTOK,
    TokenUsage,
    price_api_calls,
    price_event,
    price_tokens,
)


def test_pinned_constants_have_not_drifted():
    """Pricing constants are pinned. A silent price change breaks the build."""
    assert PRICE_API_CALL == 2_000_000
    assert PRICE_INPUT_PER_MTOK == 75_000_000
    assert PRICE_CACHED_INPUT_PER_MTOK == 7_500_000
    assert PRICE_OUTPUT_PER_MTOK == 375_000_000
    assert pricing.PLAN_QUOTAS["free"] == {"api_calls": 1_000, "tokens": 100_000}
    assert pricing.PLAN_QUOTAS["pro"] == {"api_calls": 50_000, "tokens": 5_000_000}


def test_cached_input_is_cheaper_than_fresh_input():
    """Rule 1: cached input tokens are billed at their own, cheaper rate."""
    assert PRICE_CACHED_INPUT_PER_MTOK < PRICE_INPUT_PER_MTOK

    fresh = price_tokens(TokenUsage(input_tokens=1_000_000))
    cached = price_tokens(TokenUsage(cached_input_tokens=1_000_000))
    assert fresh == 75_000_000
    assert cached == 7_500_000
    assert cached * 10 == fresh  # exactly 10x cheaper, no float fuzz


def test_reasoning_tokens_are_billed_at_the_output_rate():
    """Rule 2: reasoning tokens count as output — same rate, not free."""
    assert PRICE_REASONING_PER_MTOK is PRICE_OUTPUT_PER_MTOK

    only_output = price_tokens(TokenUsage(output_tokens=1000))
    only_reasoning = price_tokens(TokenUsage(reasoning_tokens=1000))
    assert only_reasoning == only_output
    assert only_reasoning > 0  # never a free category


def test_token_categories_are_not_simply_added_together():
    """Rule 3: pricing the sum would be wrong, because the rates differ.

    1000 tokens of each category costs far more than 4000 tokens at any single
    rate — which is exactly why the categories cannot be collapsed.
    """
    usage = TokenUsage(
        input_tokens=1000,
        cached_input_tokens=1000,
        output_tokens=1000,
        reasoning_tokens=1000,
    )
    actual = price_tokens(usage)

    naive_all_input = price_tokens(TokenUsage(input_tokens=4000))
    naive_all_output = price_tokens(TokenUsage(output_tokens=4000))

    assert actual != naive_all_input
    assert actual != naive_all_output
    # 1000*75 + 1000*7.5 + 2000*375 = 75_000 + 7_500 + 750_000
    assert actual == 832_500


def test_quota_counting_sums_all_four_categories():
    """Quota counts every token; pricing keeps them apart. Both, deliberately."""
    usage = TokenUsage(
        input_tokens=10,
        cached_input_tokens=20,
        output_tokens=30,
        reasoning_tokens=40,
    )
    assert usage.total_tokens == 100


def test_worked_example_matches_the_documented_total():
    """The exact bundle used in README/EVIDENCE, computed by hand.

      input   1200 * 75_000_000  / 1M =    90_000
      cached   800 *  7_500_000  / 1M =     6_000
      output+reasoning 800 * 375_000_000 / 1M = 300_000
      1 API call                             = 2_000_000
                                             -----------
                                               2_396_000 micro-cents
    """
    usage = TokenUsage(
        input_tokens=1200,
        cached_input_tokens=800,
        output_tokens=500,
        reasoning_tokens=300,
    )
    assert price_tokens(usage) == 396_000
    assert price_api_calls(1) == 2_000_000
    assert price_event(1, usage) == 2_396_000
    assert to_usd_string(2_396_000) == "0.023960"


def test_every_money_function_returns_an_integer():
    """No float ever touches a money value (rule M1)."""
    usage = TokenUsage(input_tokens=7, cached_input_tokens=3, output_tokens=11)
    for value in (
        price_tokens(usage),
        price_api_calls(3),
        price_event(3, usage),
    ):
        assert isinstance(value, int)
        assert not isinstance(value, bool)


def test_rounding_floors_and_never_charges_more_than_owed():
    """A sub-micro-cent fraction is never rounded up against the customer."""
    # 1 input token costs 75_000_000/1_000_000 = 75 exactly.
    assert price_tokens(TokenUsage(input_tokens=1)) == 75
    # 1 cached token costs 7.5 -> floors to 7, not 8.
    assert price_tokens(TokenUsage(cached_input_tokens=1)) == 7
