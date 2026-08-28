"""Send a Stripe-signed webhook to the local server, without Stripe.

Builds the Stripe-Signature header exactly as Stripe documents it --
HMAC-SHA256 over "{timestamp}.{raw_body}", keyed by the whsec_ secret -- so
signature verification, the tolerance window and replay dedup can all be
exercised offline. Use --forge to sign with the WRONG secret.

    python tools/sign_webhook.py checkout.session.completed --tenant tnt_acme
    python tools/sign_webhook.py checkout.session.completed --tenant tnt_acme --forge

With real Stripe keys you would use the Stripe CLI instead:
    stripe listen --forward-to localhost:8000/webhooks/stripe
    stripe trigger checkout.session.completed
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
import urllib.request

DEFAULT_URL = "http://localhost:8000/webhooks/stripe"


def sign(payload: bytes, secret: str, timestamp: int) -> str:
    signed_payload = f"{timestamp}.".encode() + payload
    mac = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={mac}"


def build_event(event_type: str, tenant: str, event_id: str) -> dict:
    if event_type == "checkout.session.completed":
        obj = {
            "id": "cs_test_evidence",
            "object": "checkout.session",
            "client_reference_id": tenant,
            "customer": "cus_test_evidence",
            "subscription": "sub_test_evidence",
            "metadata": {"tenant_id": tenant, "plan_code": "pro"},
        }
    else:
        obj = {
            "id": "sub_test_evidence",
            "object": "subscription",
            "status": "past_due" if "updated" in event_type else "canceled",
            "metadata": {"tenant_id": tenant},
        }
    return {
        "id": event_id,
        "object": "event",
        "type": event_type,
        "created": int(time.time()),
        "data": {"object": obj},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("event_type")
    parser.add_argument("--tenant", default="tnt_acme")
    parser.add_argument("--event-id", default=None)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--forge", action="store_true", help="sign with a wrong secret")
    parser.add_argument("--age", type=int, default=0, help="backdate the timestamp")
    args = parser.parse_args()

    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise SystemExit("STRIPE_WEBHOOK_SECRET is not set in the environment/.env")

    event_id = args.event_id or f"evt_{int(time.time() * 1000)}"
    body = json.dumps(build_event(args.event_type, args.tenant, event_id)).encode()

    used_secret = "whsec_an_attacker_does_not_know_the_secret" if args.forge else secret
    header = sign(body, used_secret, int(time.time()) - args.age)

    request = urllib.request.Request(
        args.url,
        data=body,
        headers={"Content-Type": "application/json", "Stripe-Signature": header},
    )
    try:
        with urllib.request.urlopen(request) as response:
            print(f"HTTP {response.status}")
            print(response.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}")
        print(exc.read().decode())


if __name__ == "__main__":
    main()
