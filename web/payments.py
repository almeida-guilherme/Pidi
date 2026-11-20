"""
Buying credits. The person picks how many (PDD_CREDIT_MIN to PDD_CREDIT_MAX, at PDD_CREDIT_PRICE_CENTS each); the site
makes a Purchase and an Asaas checkout for it and sends them to Asaas's page to pay with Pix or card. Coming
back proves nothing: the credits are added only when Asaas's webhook says the checkout was paid, which it sends
with the token set in its webhook settings (PDD_ASAAS_WEBHOOK_TOKEN). Webhooks can arrive more than once, so a
purchase adds its credits once.

Set up in Asaas: Integrações → Chaves de API (PDD_ASAAS_API_KEY), and Integrações → Webhooks with the URL
<site>/api/payments/webhook, the token, and the Checkout events (CHECKOUT_PAID, CHECKOUT_CANCELED,
CHECKOUT_EXPIRED). See deploy/LIGHTSAIL.md.
"""
import logging
import secrets
from datetime import datetime
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlmodel import select

from web import credits
from web.auth import current_user
from web.db import CreditEntry, Purchase, User, session, utcnow

log = logging.getLogger("pdd.payments")
router = APIRouter(prefix="/api/payments", tags=["payments"])


class PurchaseOut(BaseModel):
    id: str
    credits: int
    price_cents: int
    status: str
    created_at: datetime
    paid_at: datetime | None


class NewPurchase(BaseModel):
    credits: int


class CheckoutOut(BaseModel):
    id: str
    link: str  # Asaas's page, where the person pays


def _out(purchase: Purchase) -> PurchaseOut:
    return PurchaseOut(**purchase.model_dump(include=set(PurchaseOut.model_fields)))


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CheckoutOut)
def buy(body: NewPurchase, request: Request, user: User = Depends(current_user)):
    settings = request.app.state.settings
    if not settings.payments_configured:
        raise HTTPException(status.HTTP_409_CONFLICT, "Buying credits isn't set up on this server yet.")
    if not settings.credit_min <= body.credits <= settings.credit_max:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Choose between {settings.credit_min} and {settings.credit_max} credits.")
    # Each try makes a checkout at Asaas; a few an hour is plenty for a person.
    request.app.state.limits.purchase_user.take(str(user.id))
    purchase = Purchase(user_id=user.id, credits=body.credits, price_cents=body.credits * settings.credit_price_cents)
    base = settings.public_url or str(request.base_url).rstrip("/")
    try:
        purchase.checkout_id, link = request.app.state.create_checkout(
            settings.asaas_url, settings.asaas_api_key, purchase.id, purchase.credits, purchase.price_cents,
            f"{base}/credits/{purchase.id}")
    except ValueError as exc:
        log.warning("checkout for %s failed: %s", user.email, exc)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    with session(request.app.state.engine) as db:
        db.add(purchase)
        db.commit()
    return CheckoutOut(id=purchase.id, link=link)


@router.get("", response_model=list[PurchaseOut])
def list_purchases(request: Request, user: User = Depends(current_user)):
    """The person's purchases, newest first, without the ones they left unpaid."""
    with session(request.app.state.engine) as db:
        purchases = db.exec(select(Purchase).where(Purchase.user_id == user.id, Purchase.status == "paid")
                            .order_by(Purchase.created_at.desc()).limit(50)).all()
    return [_out(p) for p in purchases]


@router.get("/{purchase_id}", response_model=PurchaseOut)
def get_purchase(purchase_id: str, request: Request, user: User = Depends(current_user)):
    """Polled by the page the person comes back to from Asaas, until the payment is confirmed."""
    with session(request.app.state.engine) as db:
        purchase = db.get(Purchase, purchase_id)
    if purchase is None or purchase.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Purchase not found.")
    return _out(purchase)


# --- Asaas's webhook ---

def mark_paid(engine, purchase_id: str) -> bool:
    """Adds the purchase's credits, once; False if they were added before."""
    with credits._lock, session(engine) as db:
        purchase = db.get(Purchase, purchase_id)
        if purchase is None or purchase.status == "paid":
            return False
        purchase.status, purchase.paid_at = "paid", utcnow()
        db.add(purchase)
        db.add(CreditEntry(user_id=purchase.user_id, amount=purchase.credits, kind="purchase",
                           note=f"Bought {purchase.credits} credits"))
        db.commit()
    return True


def _find(db, checkout: dict, payment: dict) -> Purchase | None:
    """The purchase an event is about: by Asaas's checkout id, or by our id sent as externalReference."""
    if checkout.get("id"):
        found = db.exec(select(Purchase).where(Purchase.checkout_id == checkout["id"])).first()
        if found:
            return found
    for reference in (checkout.get("externalReference"), payment.get("externalReference")):
        if reference and (found := db.get(Purchase, reference)):
            return found
    return None


@router.post("/webhook", include_in_schema=False)
def webhook(request: Request, event: dict = Body(...)):
    """Asaas's notifications. Anything other than 200 makes Asaas send it again (and, after 15 failures in a
    row, stop sending), so events that don't concern a purchase are acknowledged too."""
    settings = request.app.state.settings
    token = request.headers.get("asaas-access-token", "")
    if not settings.asaas_webhook_token or not secrets.compare_digest(token, settings.asaas_webhook_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token.")
    kind = event.get("event", "")
    checkout = event.get("checkout") or {}
    payment = event.get("payment") or {}
    engine = request.app.state.engine
    with session(engine) as db:
        purchase = _find(db, checkout, payment)
    if purchase is None:
        if kind.startswith("CHECKOUT_"):
            log.warning("Asaas event %s %s for an unknown checkout %s", event.get("id"), kind, checkout.get("id"))
        return {"ok": True}
    if kind == "CHECKOUT_PAID" or (kind in ("PAYMENT_CONFIRMED", "PAYMENT_RECEIVED") and payment):
        if mark_paid(engine, purchase.id):
            log.info("purchase %s paid: %s credits for user %s", purchase.id, purchase.credits, purchase.user_id)
    elif kind in ("CHECKOUT_CANCELED", "CHECKOUT_EXPIRED"):
        with session(engine) as db:
            purchase = db.get(Purchase, purchase.id)
            if purchase.status == "pending":
                purchase.status = "canceled" if kind == "CHECKOUT_CANCELED" else "expired"
                db.add(purchase)
                db.commit()
    return {"ok": True}
