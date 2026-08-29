"""Create (or reuse) the test-mode Pro product + recurring price.

Reads STRIPE_SECRET_KEY from .env, creates the Pro price if it does not already
exist, and writes its id back to STRIPE_PRICE_ID_PRO in .env.

Idempotent: it tags the price with metadata.capstone_plan=pro and reuses an
existing one, so running it twice does not create a second price.

    python tools/setup_stripe_pro_price.py

Prints only the price id, which is not a secret. The API key is never echoed.
"""

from __future__ import annotations

import pathlib
import re

from dotenv import dotenv_values

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def main() -> None:
    config = dotenv_values(ENV_PATH)
    api_key = (config.get("STRIPE_SECRET_KEY") or "").strip()

    if not api_key:
        raise SystemExit("STRIPE_SECRET_KEY is not set in .env")
    if not api_key.startswith("sk_test_"):
        # The whole project is test mode only; refuse rather than trust a typo.
        raise SystemExit("STRIPE_SECRET_KEY is not a test key. Refusing to run.")

    import stripe

    stripe.api_key = api_key

    account = stripe.Account.retrieve()
    print(f"connected to Stripe account {account.id}")

    existing = [
        p
        for p in stripe.Price.list(limit=100, active=True).data
        if (p.metadata or {}).get("capstone_plan") == "pro"
    ]

    if existing:
        price = existing[0]
        print("reusing the existing Pro price")
    else:
        product = stripe.Product.create(
            name="Metering & Billing Engine — Pro",
            description="50,000 API calls and 5,000,000 AI tokens per month.",
            metadata={"capstone_plan": "pro"},
        )
        price = stripe.Price.create(
            product=product.id,
            unit_amount=2900,  # $29.00/month, as integer cents
            currency="usd",
            recurring={"interval": "month"},
            metadata={"capstone_plan": "pro"},
        )
        print(f"created product {product.id}")

    print(f"price id: {price.id}  ({price.unit_amount} cents / month)")

    text = ENV_PATH.read_text(encoding="utf-8")
    text = re.sub(
        r"^STRIPE_PRICE_ID_PRO=.*$",
        f"STRIPE_PRICE_ID_PRO={price.id}",
        text,
        flags=re.M,
    )
    ENV_PATH.write_text(text, encoding="utf-8")
    print("STRIPE_PRICE_ID_PRO written to .env")


if __name__ == "__main__":
    main()
