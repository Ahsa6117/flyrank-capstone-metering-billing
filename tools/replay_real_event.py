"""Replay a REAL Stripe event against the local webhook endpoint.

Fetches an actual event from the Stripe API by id, re-signs it with the real
whsec_ signing secret exactly as Stripe does, and POSTs it. Used to prove:

  * a genuine replay of a real event (same event.id) is processed only once
  * a forged signature is rejected with 400 and changes nothing

    python tools/replay_real_event.py evt_123           # valid signature
    python tools/replay_real_event.py evt_123 --forge   # wrong secret

Prints no secret material.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import pathlib
import time
import urllib.error
import urllib.request

from dotenv import dotenv_values

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
URL = "http://localhost:8000/webhooks/stripe"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("event_id")
    parser.add_argument("--forge", action="store_true")
    args = parser.parse_args()

    config = dotenv_values(ENV_PATH)
    api_key = (config.get("STRIPE_SECRET_KEY") or "").strip()
    whsec = (config.get("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not api_key.startswith("sk_test_"):
        raise SystemExit("STRIPE_SECRET_KEY must be a test key")

    import stripe

    stripe.api_key = api_key

    # A genuine event, fetched from Stripe by id.
    event = stripe.Event.retrieve(args.event_id)
    body = json.dumps(event.to_dict(), separators=(",", ":"), default=str).encode()

    secret = "whsec_a_secret_the_attacker_guessed" if args.forge else whsec
    timestamp = int(time.time())
    mac = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()

    request = urllib.request.Request(
        URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": f"t={timestamp},v1={mac}",
        },
    )
    label = "FORGED signature" if args.forge else "valid signature"
    print(f"POST {URL}  [{label}]  event={event.id} type={event.type}")
    try:
        with urllib.request.urlopen(request) as response:
            print(f"HTTP {response.status}")
            print(response.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}")
        print(exc.read().decode())


if __name__ == "__main__":
    main()
