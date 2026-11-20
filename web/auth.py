import logging
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import select

from web import google
from web.db import CreditEntry, User, session, utcnow
from web.ratelimit import client_ip

COOKIE = "pdd_session"
# Holds the state and nonce of a Google sign-in in progress, until Google sends the person back.
SIGNIN_COOKIE = "pdd_signin"
SIGNIN_PATH = "/api/auth/google"
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# The privacy notice's version (the date it last changed). People who accepted an older one are asked again.
PRIVACY_VERSION = "2026-11-20"  # who receives the recordings: the AI provider each person connects

log = logging.getLogger("pdd.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])


def normalize_email(value: str) -> str:
    value = value.strip().lower()
    if not EMAIL.match(value):
        raise ValueError("Enter a valid email address.")
    return value


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    privacy_accepted: bool  # the current version of the privacy notice

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(id=user.id, email=user.email, name=user.name or "",
                   privacy_accepted=user.privacy_version == PRIVACY_VERSION)


class ProfileUpdate(BaseModel):
    name: str


class SignInError(Exception):
    """A sign-in that can't go on; the message is shown on the log-in page."""


def _check_email_allowed(settings, email: str) -> None:
    domain = email.rsplit("@", 1)[1]
    if settings.signup_domains and domain not in settings.signup_domains:
        raise SignInError(f"Accounts are limited to {', '.join('@' + d for d in settings.signup_domains)} addresses.")


def _set_session(response: Response, request: Request, user: User) -> None:
    settings = request.app.state.settings
    expires = utcnow() + timedelta(days=settings.session_days)
    token = jwt.encode({"sub": str(user.id), "ver": user.session_version or 0, "exp": expires}, settings.secret_key,
                       algorithm="HS256")
    # httpOnly: page scripts can't read it; SameSite=Lax: other sites can't send it with their requests.
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=settings.session_days * 86400, path="/")


def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in.")
    try:
        claims = jwt.decode(token, request.app.state.settings.secret_key, algorithms=["HS256"])
        user_id, version = int(claims["sub"]), int(claims.get("ver", 0))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired; log in again.")
    with session(request.app.state.engine) as db:
        user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found.")
    if version != (user.session_version or 0):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session ended; log in again.")
    return user


def active_user(user: User = Depends(current_user)) -> User:
    """A user who may upload: the current privacy notice accepted."""
    if user.privacy_version != PRIVACY_VERSION:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Accept the privacy notice first.")
    return user


# --- Log in with Google ---

def _redirect_uri(request: Request) -> str:
    base = request.app.state.settings.public_url or str(request.base_url).rstrip("/")
    return f"{base}{SIGNIN_PATH}/callback"


def _safe_next(value: str | None) -> str:
    """Only a path on this site, so the sign-in can't be used to send people elsewhere."""
    return value if value and value.startswith("/") and not value.startswith(("//", "/\\")) else "/"


def _to_login(error: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?{urlencode({'error': error})}", status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SIGNIN_COOKIE, path=SIGNIN_PATH)
    return response


@router.get("/google", include_in_schema=False)
def google_start(request: Request, next: str | None = None):
    settings = request.app.state.settings
    if not settings.google_configured:
        return _to_login("Log in with Google isn't set up on this server yet.")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    domain = settings.signup_domains[0] if len(settings.signup_domains) == 1 else None
    response = RedirectResponse(google.authorize_url(settings.google_client_id, _redirect_uri(request), state, nonce,
                                                     domain), status.HTTP_303_SEE_OTHER)
    signin = jwt.encode({"state": state, "nonce": nonce, "next": _safe_next(next),
                         "exp": utcnow() + timedelta(minutes=10)}, settings.secret_key, algorithm="HS256")
    # Lax still sends it when Google sends the browser back (a top-level navigation).
    response.set_cookie(SIGNIN_COOKIE, signin, httponly=True, samesite="lax", secure=settings.cookie_secure,
                        max_age=600, path=SIGNIN_PATH)
    return response


@router.get("/google/callback", include_in_schema=False)
def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    settings = request.app.state.settings
    try:
        signin = jwt.decode(request.cookies.get(SIGNIN_COOKIE, ""), settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return _to_login("The log-in took too long or was opened in another browser. Try again.")
    if error or not code:
        return _to_login("Log in with Google was cancelled." if error == "access_denied"
                         else f"Google didn't log you in ({error or 'no code'}).")
    if not state or not secrets.compare_digest(state, signin["state"]):
        return _to_login("The log-in couldn't be verified. Try again.")
    limiter, ip = request.app.state.limits.signin_ip, client_ip(request)
    try:
        limiter.check(ip)
        claims = request.app.state.google_identity(code, _redirect_uri(request), settings.google_client_id,
                                                   settings.google_client_secret)
        if claims.get("nonce") != signin["nonce"]:
            raise SignInError("The log-in couldn't be verified. Try again.")
        user = _user_for(request, claims)
    except (SignInError, ValueError) as exc:
        limiter.hit(ip)
        log.warning("Google log-in failed from %s: %s", ip, exc)
        return _to_login(str(exc))
    except HTTPException as exc:  # too many failed tries
        return _to_login(exc.detail)
    response = RedirectResponse(signin["next"], status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SIGNIN_COOKIE, path=SIGNIN_PATH)
    _set_session(response, request, user)
    return response


# --- Local login (PDD_LOCAL_LOGIN), for running Pidi on your own machine ---

LOCAL_PATH = "/api/auth/local"


def local_login_url(app) -> str:
    base = app.state.settings.public_url or "http://127.0.0.1:8000"
    return f"{base}{LOCAL_PATH}?token={app.state.local_login_token}"


def announce_local_login(app) -> None:
    """The link is the only way in, so it goes to the server's output, where only whoever runs it sees it."""
    print(f"\n  Pidi: log in as {app.state.settings.local_email} by opening\n  {local_login_url(app)}\n"
          "  (on another address or port, keep the part from /api/auth/local)\n", flush=True)


@router.get("/local", include_in_schema=False)
def local_login(request: Request, token: str = ""):
    settings, expected = request.app.state.settings, request.app.state.local_login_token
    if not settings.local_login or not expected:
        return _to_login("Local log-in isn't turned on on this server.")
    limiter, ip = request.app.state.limits.signin_ip, client_ip(request)
    try:
        limiter.check(ip)
    except HTTPException as exc:
        return _to_login(exc.detail)
    if not secrets.compare_digest(token.encode(), expected.encode()):
        limiter.hit(ip)
        log.warning("Local log-in with a wrong link from %s", ip)
        return _to_login("That log-in link is wrong or from before the server restarted; use the one in its log.")
    email = normalize_email(settings.local_email)
    with session(request.app.state.engine) as db:
        user = db.exec(select(User).where(User.email == email)).first()
        if user is None:
            user = User(email=email, name="Owner")
            db.add(user)
            db.commit()
            db.refresh(user)
            welcome(db, settings, user)
    response = RedirectResponse("/", status.HTTP_303_SEE_OTHER)
    _set_session(response, request, user)
    return response


def _user_for(request: Request, claims: dict) -> User:
    """The account of the Google account in `claims`, made on its first log-in."""
    settings = request.app.state.settings
    if not claims.get("email_verified"):
        raise SignInError("Your Google account's email isn't verified by Google.")
    email = normalize_email(claims["email"])
    _check_email_allowed(settings, email)
    sub, name = str(claims["sub"]), (claims.get("name") or "").strip()[:120] or None
    with session(request.app.state.engine) as db:
        user = db.exec(select(User).where(User.google_sub == sub)).first()
        if user is None:
            # Accounts made before Google log-in (or by web.create_user) are joined by their email.
            user = db.exec(select(User).where(User.email == email)).first()
            if user is not None and user.google_sub:
                raise SignInError(f"{email} belongs to another Google account's Pidi account.")
        if user is None:
            if not settings.allow_signup:
                raise SignInError("Sign-up is closed; ask an administrator to add your email.")
            user = User(email=email, name=name)
        elif user.email != email and db.exec(select(User).where(User.email == email)).first():
            raise SignInError(f"{email} is used by another Pidi account.")
        user.google_sub, user.email, user.name = sub, email, user.name or name
        new = user.id is None
        db.add(user)
        db.commit()
        db.refresh(user)
        if new:
            welcome(db, settings, user)
    return user


def welcome(db, settings, user: User) -> None:
    """A new account's first credits (PDD_SIGNUP_CREDITS), to try Pidi with (see web/credits.py); none when
    people run projects on their own keys."""
    if settings.uses_credits and settings.signup_credits > 0:
        db.add(CreditEntry(user_id=user.id, amount=settings.signup_credits, kind="grant", note="Welcome credits"))
        db.commit()


# --- Endpoints ---

@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return UserOut.of(user)


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfileUpdate, request: Request, user: User = Depends(current_user)):
    """The name; the email is the Google account's and follows it on each log-in."""
    with session(request.app.state.engine) as db:
        user = db.get(User, user.id)
        user.name = body.name.strip()[:120] or None
        db.add(user)
        db.commit()
    return UserOut.of(user)


@router.post("/privacy", response_model=UserOut)
def accept_privacy(request: Request, user: User = Depends(current_user)):
    """Records that the user accepted the current privacy notice (new accounts, or after it changed)."""
    with session(request.app.state.engine) as db:
        user = db.get(User, user.id)
        user.privacy_accepted_at, user.privacy_version = utcnow(), PRIVACY_VERSION
        db.add(user)
        db.commit()
    return UserOut.of(user)
