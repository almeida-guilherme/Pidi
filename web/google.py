"""
Log in with Google (OpenID Connect, authorization code flow). The browser is sent to Google with a random
`state` and `nonce`; Google sends it back to /api/auth/google/callback with a code, which the server trades
for an ID token: Google's signed statement of who the person is (their account id `sub`, email, name).

Set up in Google Cloud Console → APIs & Services → Credentials → OAuth client ID (Web application), with
the redirect URI <site address>/api/auth/google/callback; see deploy/LIGHTSAIL.md.
"""
from urllib.parse import urlencode
import httpx
import jwt

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
ISSUERS = ["https://accounts.google.com", "accounts.google.com"]
# Google's public keys, fetched when first needed and cached (they change every few weeks).
_keys = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", cache_keys=True)


def authorize_url(client_id: str, redirect_uri: str, state: str, nonce: str, domain: str | None = None) -> str:
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account"}
    if domain:
        params["hd"] = domain  # Google offers only that domain's accounts (checked again on return)
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def identity(code: str, redirect_uri: str, client_id: str, client_secret: str) -> dict:
    """The verified claims of the ID token the code is traded for; ValueError if Google refuses it."""
    try:
        response = httpx.post(TOKEN_URL, timeout=15, data={
            "code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri,
            "grant_type": "authorization_code"})
    except httpx.HTTPError as exc:
        raise ValueError(f"Couldn't reach Google ({type(exc).__name__}).") from exc
    if response.status_code != 200:
        raise ValueError(f"Google refused the sign-in ({response.json().get('error', response.status_code)}).")
    token = response.json()["id_token"]
    try:
        key = _keys.get_signing_key_from_jwt(token)
        return jwt.decode(token, key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUERS)
    except jwt.PyJWTError as exc:
        raise ValueError(f"Google's answer couldn't be verified ({exc}).") from exc
