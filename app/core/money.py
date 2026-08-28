"""Integer money formatting.

No float ever touches a money value on the way *in* (see docs/REFERENCES.md M1).
Floats appear only in the final human-readable string, and only via
``decimal.Decimal`` so the printed value is exact.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.pricing import MICRO_CENTS_PER_CENT, MICRO_CENTS_PER_DOLLAR


def to_cents(micro_cents: int) -> int:
    """Whole cents, floored. Never rounds up against the customer."""
    return micro_cents // MICRO_CENTS_PER_CENT


def to_usd_string(micro_cents: int) -> str:
    """Exact USD string with six decimal places, e.g. ``'0.003415'``.

    Uses ``Decimal`` rather than float division: ``341500 / 100_000_000`` in
    binary floating point is not exactly 0.003415, and money must print exactly.
    """
    return str(
        (Decimal(micro_cents) / Decimal(MICRO_CENTS_PER_DOLLAR)).quantize(
            Decimal("0.000001")
        )
    )


def money_fields(micro_cents: int) -> dict[str, int | str]:
    """The standard money shape returned by the API."""
    return {
        "micro_cents": micro_cents,
        "cents": to_cents(micro_cents),
        "usd": to_usd_string(micro_cents),
    }
