"""
People's own AI provider keys, with billing "keys" (PDD_BILLING, the default): OpenAI, Anthropic (Claude), Google
(Gemini), or the server's Ollama (no key; providers.py). Each project runs on the key its owner picks, so the
model calls are billed to them; a key can name the model to use instead of the provider's defaults. With billing "credits" (web/credits.py) these endpoints answer 404
and every project runs on the server's key.

Keys are encrypted in the database with a key derived from the server's secret (PDD_SECRET_KEY) and never sent
back to the browser; only their first three and last four characters are shown. Anyone who gets both the
database and the secret can read them, so keep the data folder private and back it up encrypted.

The server's own key (OPENAI_API_KEY in .env) can be offered too, to people without a key of their own, with
PDD_ALLOW_SERVER_KEY=true.
"""
import base64
import hashlib
import os
from datetime import datetime
from typing import Literal
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlmodel import select

from web.auth import current_user
from web.db import ApiKey, User, session

router = APIRouter(prefix="/api/keys", tags=["api keys"])

class KeyOut(BaseModel):
    id: str
    provider: str
    name: str
    hint: str  # e.g. "sk-…4F2a"; empty for Ollama
    model: str | None
    created_at: datetime


class ProviderOut(BaseModel):
    id: str
    label: str
    needs_key: bool
    keys_url: str
    models: list[str]  # its defaults: screenshots, writing


class KeysOut(BaseModel):
    keys: list[KeyOut]
    server_key: bool  # whether projects can run on the server's own key
    providers: list[ProviderOut]  # the ones that can be connected here


class NewKey(BaseModel):
    provider: Literal["openai", "anthropic", "gemini", "ollama"] = "openai"
    name: str = ""
    key: str = ""
    model: str = ""


def _fernet(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"pdd-api-keys:" + secret.encode()).digest()))


def encrypt(secret: str, key: str) -> str:
    return _fernet(secret).encrypt(key.encode()).decode()


def decrypt(secret: str, token: str) -> str:
    try:
        return _fernet(secret).decrypt(token.encode()).decode()
    except InvalidToken:
        # The server's secret changed since the key was saved.
        raise ValueError("This API key can no longer be read; remove it and connect it again.")


def server_key_available(settings) -> bool:
    return settings.allow_server_key and bool(os.getenv("OPENAI_API_KEY"))


def check_key(provider: str, key: str) -> None:
    """Raises ValueError if the provider refuses the key (providers.check_key)."""
    import providers
    providers.check_key(provider, key)


def _keys_billing(request: Request) -> None:
    if request.app.state.settings.uses_credits:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This site runs projects on credits, not on your own key.")


def _out(key: ApiKey) -> KeyOut:
    return KeyOut(id=key.id, provider=key.provider, name=key.name,
                  hint=f"{key.prefix}…{key.last4}" if key.last4 else "", model=key.model, created_at=key.created_at)


def user_key(request: Request, user: User, key_id: str) -> ApiKey:
    with session(request.app.state.engine) as db:
        key = db.get(ApiKey, key_id)
    if key is None or key.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found.")
    return key


def pick_key(request: Request, user: User, key_id: str | None) -> ApiKey | None:
    """
    The key work runs on: the user's chosen key; with none chosen, their newest key; None means the
    server's own key, which is used only when the user has no key of their own and it's allowed.
    """
    if key_id:
        return user_key(request, user, key_id)
    with session(request.app.state.engine) as db:
        key = db.exec(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())).first()
    if key is None and not server_key_available(request.app.state.settings):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Connect an AI provider on the Account page first.")
    return key


def ai_for(secret: str, key: ApiKey | None) -> dict | None:
    """What a run on `key` uses (providers.py): {"provider", "api_key", "model"}; None for the server's key.
    ValueError if the key can't be read any more."""
    if key is None:
        return None
    return {"provider": key.provider, "api_key": decrypt(secret, key.secret) or None, "model": key.model}


def request_key(request: Request, user: User) -> dict:
    """For work done during a request (reading a template, assessing a project, a redo): {"ai": the person's
    provider} with billing "keys", nothing with credits (the server's key)."""
    if request.app.state.settings.uses_credits:
        return {}
    try:
        ai = ai_for(request.app.state.settings.secret_key, pick_key(request, user, None))
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return {"ai": ai} if ai else {}


@router.get("", response_model=KeysOut)
def list_keys(request: Request, user: User = Depends(current_user)):
    _keys_billing(request)
    with session(request.app.state.engine) as db:
        keys = db.exec(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at)).all()
    import providers
    offered = [ProviderOut(id=p, label=providers.PROVIDERS[p]["label"], needs_key=providers.PROVIDERS[p]["key"],
                           keys_url=providers.PROVIDERS[p]["keys_url"], models=list(dict.fromkeys(providers.DEFAULTS[p])))
               for p in providers.available()]
    return KeysOut(keys=[_out(k) for k in keys], server_key=server_key_available(request.app.state.settings),
                   providers=offered)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=KeyOut)
def add_key(body: NewKey, request: Request, user: User = Depends(current_user)):
    import providers
    _keys_billing(request)
    if body.provider not in providers.available():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That provider isn't offered on this server.")
    needs_key = providers.PROVIDERS[body.provider]["key"]
    key, model = body.key.strip(), body.model.strip()[:120] or None
    name = body.name.strip()[:60] or providers.PROVIDERS[body.provider]["label"]
    if needs_key and (len(key) < 20 or any(c.isspace() for c in key)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "That doesn't look like an API key.")
    if model and any(c.isspace() for c in model):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A model name has no spaces, e.g. claude-opus-5-5.")
    # Each try is a request to the provider; a few an hour is plenty for a person.
    request.app.state.limits.key_user.take(str(user.id))
    try:
        request.app.state.check_api_key(body.provider, key)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    record = ApiKey(user_id=user.id, provider=body.provider, name=name, model=model,
                    prefix=key[:3] if needs_key else "", last4=key[-4:] if needs_key else "",
                    secret=encrypt(request.app.state.settings.secret_key, key if needs_key else ""))
    with session(request.app.state.engine) as db:
        db.add(record)
        db.commit()
    return _out(record)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_key(key_id: str, request: Request, user: User = Depends(current_user)):
    _keys_billing(request)
    user_key(request, user, key_id)
    with session(request.app.state.engine) as db:
        db.delete(db.get(ApiKey, key_id))
        db.commit()
