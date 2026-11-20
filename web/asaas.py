"""
Asaas (https://www.asaas.com), the payment provider: credits are bought on Asaas's own checkout page (Pix or
card), so the site never sees payment details. Asaas tells the site a checkout was paid with a webhook
(CHECKOUT_PAID); see web/payments.py.

Docs: https://docs.asaas.com/reference/criar-novo-checkout and https://docs.asaas.com/docs/eventos-para-checkout
"""
import base64
from pathlib import Path
import httpx

# The item's picture on the checkout page (required by Asaas): Pidi's logo.
_IMAGE = base64.b64encode((Path(__file__).parent / "pidi-credits.png").read_bytes()).decode()


def create_checkout(base_url: str, api_key: str, reference: str, credits: int, price_cents: int,
                    return_url: str) -> tuple[str, str]:
    """A checkout page for `credits` at `price_cents`; returns its id and link. `reference` (our purchase id)
    comes back with its webhooks; `return_url` is where the payer is sent back to, with ?result=."""
    body = {
        "billingTypes": ["PIX", "CREDIT_CARD"],
        "chargeTypes": ["DETACHED"],
        "minutesToExpire": 60,
        "externalReference": reference,
        "callback": {"successUrl": f"{return_url}?result=success", "cancelUrl": f"{return_url}?result=canceled",
                     "expiredUrl": f"{return_url}?result=expired"},
        "items": [{"name": f"{credits} créditos Pidi", "description": f"{credits} credits for Pidi projects",
                   "quantity": 1, "value": price_cents / 100, "imageBase64": _IMAGE, "externalReference": reference}],
    }
    try:
        response = httpx.post(f"{base_url}/checkouts", json=body, timeout=20,
                              headers={"access_token": api_key, "User-Agent": "Pidi/1.0"})
    except httpx.HTTPError as exc:
        raise ValueError(f"Couldn't reach Asaas ({type(exc).__name__}). Try again.") from exc
    if response.status_code != 200:
        try:
            details = "; ".join(e.get("description", "") for e in response.json().get("errors", []))
        except ValueError:
            details = ""
        raise ValueError(f"Asaas refused the payment ({response.status_code}{': ' + details if details else ''}).")
    data = response.json()
    return data["id"], data["link"]
