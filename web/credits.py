"""
Credits: what projects are paid with. Every project runs on the server's OpenAI key (OPENAI_API_KEY), so the
site keeps nobody's key; people spend credits instead.

- A project is charged when it is uploaded, at a price known before anything is spent: PDD_CREDITS_PER_MINUTE
  per started minute of recording, plus TRANSLATION_CREDITS when Pidi's template must first be translated into
  its language. An upload that costs more than the balance is refused, so a project never runs out halfway.
- Credits are given back only when the project was deleted before it was processed (nothing was spent on it
  yet) or when it stops at its cost limit (PDD_MAX_COST_PER_CREDIT_USD). Once processing started, OpenAI was
  paid: a failed project is retried for free while its recording is kept, but deleting it gives nothing back.
- Credits are given by the administrator (python -m web.grant_credits) and to new accounts (PDD_SIGNUP_CREDITS).

The balance is the sum of the person's entries (CreditEntry), which are only ever added.
"""
import math
import threading
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlmodel import Session, select

from pidi_template import is_cached, normalize_language
from web.auth import current_user
from web.db import CreditEntry, Run, User, session, utcnow

router = APIRouter(prefix="/api/credits", tags=["credits"])

# For translating Pidi's template into a language it hasn't been translated into yet (pidi_template.py).
TRANSLATION_CREDITS = 1
# Charging checks the balance and takes the credits in one step, so two uploads at once can't both spend the
# same credits. The lock covers SQLite (one server process, see the Dockerfile); the row lock in charge()
# covers databases that support it.
_lock = threading.Lock()


class NotEnoughCredits(Exception):
    def __init__(self, needed: int, balance: int):
        super().__init__(f"This recording needs {needed} credit{'s' if needed != 1 else ''} and you have {balance}.")
        self.needed, self.balance = needed, balance


def quote(settings, seconds: float, language: str | None) -> int:
    """The credits a project costs: per started minute of recording, plus a template translation if needed.
    `language` is None for a company template (written in its own language)."""
    credits = max(1, math.ceil(seconds / 60)) * settings.credits_per_minute
    if language is not None and not is_cached(language, str(settings.translations_dir)):
        credits += TRANSLATION_CREDITS
    return credits


def balance(db: Session, user_id: int) -> int:
    return db.exec(select(func.coalesce(func.sum(CreditEntry.amount), 0))
                   .where(CreditEntry.user_id == user_id)).one()


def add_charged_run(engine, run: Run, credits: int) -> None:
    """Saves `run` and charges its owner `credits` for it, together; NotEnoughCredits if they don't have them."""
    with _lock, session(engine) as db:
        db.exec(select(User).where(User.id == run.user_id).with_for_update()).one()
        have = balance(db, run.user_id)
        if have < credits:
            raise NotEnoughCredits(credits, have)
        run.credits = credits
        db.add(run)
        db.add(CreditEntry(user_id=run.user_id, amount=-credits, kind="charge", run_id=run.id,
                           note=run.name or run.video_name))
        db.commit()


def refund(engine, run_id: str, why: str) -> bool:
    """Gives back what the project was charged, once; False if there was nothing to give back."""
    with _lock, session(engine) as db:
        run = db.get(Run, run_id)
        if run is None or not run.credits or run.refunded_at is not None:
            return False
        run.refunded_at = utcnow()
        db.add(run)
        db.add(CreditEntry(user_id=run.user_id, amount=run.credits, kind="refund", run_id=run.id,
                           note=f"{run.name or run.video_name}: {why}"))
        db.commit()
    return True


def grant(engine, user_id: int, credits: int, note: str) -> int:
    """Adds credits (or takes them back, when negative); returns the new balance."""
    with _lock, session(engine) as db:
        db.add(CreditEntry(user_id=user_id, amount=credits, kind="grant", note=note))
        db.commit()
        return balance(db, user_id)


# --- Endpoints ---

class EntryOut(BaseModel):
    amount: int
    kind: str
    note: str | None
    run_id: str | None
    created_at: datetime


class CreditsOut(BaseModel):
    balance: int
    entries: list[EntryOut]  # newest first


class QuoteOut(BaseModel):
    credits: int
    balance: int


@router.get("", response_model=CreditsOut)
def get_credits(request: Request, user: User = Depends(current_user)):
    with session(request.app.state.engine) as db:
        entries = db.exec(select(CreditEntry).where(CreditEntry.user_id == user.id)
                          .order_by(CreditEntry.created_at.desc(), CreditEntry.id.desc())).all()
        return CreditsOut(balance=balance(db, user.id), entries=[EntryOut(**e.model_dump()) for e in entries])


@router.get("/quote", response_model=QuoteOut)
def get_quote(request: Request, seconds: float, language: str | None = None, user: User = Depends(current_user)):
    """The price of a recording of `seconds`, before uploading it (the browser reads its length); the
    upload measures the recording itself and charges that. No `language`: a company template."""
    if not 0 <= seconds <= 24 * 3600:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That isn't a recording's length.")
    if language is not None:
        try:
            language = normalize_language(language)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    with session(request.app.state.engine) as db:
        have = balance(db, user.id)
    return QuoteOut(credits=quote(request.app.state.settings, seconds, language), balance=have)
