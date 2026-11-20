import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """Read from environment variables (or .env); see the "Website" section of docs/reference.md."""
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("PDD_DATA_DIR", "data")))
    database_url: str = field(default_factory=lambda: os.getenv("PDD_DATABASE_URL", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("PDD_SECRET_KEY", ""))
    allow_signup: bool = field(default_factory=lambda: _bool("PDD_ALLOW_SIGNUP", True))
    # Log in with Google: the OAuth client from Google Cloud Console (see web/google.py). Without them
    # nobody can log in.
    google_client_id: str = field(default_factory=lambda: os.getenv("PDD_GOOGLE_CLIENT_ID", "").strip())
    google_client_secret: str = field(default_factory=lambda: os.getenv("PDD_GOOGLE_CLIENT_SECRET", "").strip())
    # For running Pidi on your own machine without a Google OAuth client: at startup the server prints a secret
    # link to its log, and opening it logs in as PDD_LOCAL_EMAIL (the account is made the first time). Anyone
    # with the server's log can log in, so leave it off on a shared server.
    local_login: bool = field(default_factory=lambda: _bool("PDD_LOCAL_LOGIN", False))
    local_email: str = field(default_factory=lambda: os.getenv("PDD_LOCAL_EMAIL", "owner@pidi.local").strip())
    # Comma-separated email domains allowed to sign up (e.g. "company.com"); empty allows any.
    signup_domains: list[str] = field(default_factory=lambda: [
        d.strip().lower().lstrip("@") for d in os.getenv("PDD_SIGNUP_DOMAINS", "").split(",") if d.strip()])
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("PDD_MAX_UPLOAD_MB", "2048")))
    # Runs processed at the same time, and at most this many per person, so one person's queue doesn't
    # hold up everyone else. Each run calls the OpenAI API for minutes and holds video frames in memory.
    workers: int = field(default_factory=lambda: int(os.getenv("PDD_WORKERS", "2")))
    runs_per_user: int = field(default_factory=lambda: int(os.getenv("PDD_RUNS_PER_USER", "1")))
    # Days after a project finishes when its recording (video, audio, frames not in the document) is
    # deleted; the document and its screenshots stay. 0 keeps recordings until the project is deleted.
    retention_days: int = field(default_factory=lambda: int(os.getenv("PDD_RETENTION_DAYS", "30")))
    # The site's address (e.g. https://pidi.example.com), for Google's redirect URI. Without it, the address
    # the request came to.
    public_url: str = field(default_factory=lambda: os.getenv("PDD_PUBLIC_URL", "").rstrip("/"))
    # Outgoing email (alerts). Without PDD_SMTP_HOST, emails are written
    # to the server log instead, which is enough for development.
    smtp_host: str = field(default_factory=lambda: os.getenv("PDD_SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("PDD_SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: os.getenv("PDD_SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("PDD_SMTP_PASSWORD", ""))
    mail_from: str = field(default_factory=lambda: os.getenv("PDD_MAIL_FROM", ""))
    # Where failed runs and server errors are reported; empty: only the server log.
    alert_email: str = field(default_factory=lambda: os.getenv("PDD_ALERT_EMAIL", "").strip())
    # Who runs the site, shown in the privacy notice (LGPD: the controller and how to reach them).
    operator_name: str = field(default_factory=lambda: os.getenv("PDD_OPERATOR_NAME", "").strip())
    contact_email: str = field(default_factory=lambda: os.getenv("PDD_CONTACT_EMAIL", "").strip())
    # Set to true when the site is served over HTTPS, so the login cookie is never sent over plain HTTP.
    cookie_secure: bool = field(default_factory=lambda: _bool("PDD_COOKIE_SECURE", False))
    # How projects are paid for. "keys": each person connects their own OpenAI key (web/keys.py) and the model
    # calls are billed to it. "credits": every project runs on the server's OPENAI_API_KEY and people pay with
    # credits (web/credits.py, web/payments.py), which stay in the code for that.
    billing: str = field(default_factory=lambda: os.getenv("PDD_BILLING", "keys").strip().lower())
    # With billing "keys": let people without a key of their own run projects on the server's OPENAI_API_KEY
    # (at the server owner's expense; for a site only trusted people use).
    allow_server_key: bool = field(default_factory=lambda: _bool("PDD_ALLOW_SERVER_KEY", False))
    # Credits (web/credits.py): projects run on the server's OPENAI_API_KEY and are paid for with credits.
    # A project costs this many credits per started minute of recording.
    credits_per_minute: int = field(default_factory=lambda: int(os.getenv("PDD_CREDITS_PER_MINUTE", "1")))
    # Given to each new account on its first log-in.
    signup_credits: int = field(default_factory=lambda: int(os.getenv("PDD_SIGNUP_CREDITS", "10")))
    # The most a project's model calls may cost per credit it was charged; one that goes over stops and its
    # credits are given back. Keep it below what a credit brings in after Asaas's fee (at R$1.50 a credit, about
    # R$1.30, or $0.24, on a 10-credit pack paid by Pix), so a finished project never loses money. A run costs about
    # $0.06 per minute of recording, so $0.15 still leaves room for heavy ones.
    max_cost_per_credit_usd: float = field(default_factory=lambda: float(os.getenv("PDD_MAX_COST_PER_CREDIT_USD", "0.15")))
    # Buying credits through Asaas (web/payments.py). Without the API key, credits can only be given by the
    # administrator. PDD_ASAAS_SANDBOX=true uses Asaas's test environment (no real money).
    asaas_api_key: str = field(default_factory=lambda: os.getenv("PDD_ASAAS_API_KEY", "").strip())
    asaas_sandbox: bool = field(default_factory=lambda: _bool("PDD_ASAAS_SANDBOX", True))
    # Sent by Asaas with every webhook (header asaas-access-token); set the same value in Asaas's webhook settings.
    asaas_webhook_token: str = field(default_factory=lambda: os.getenv("PDD_ASAAS_WEBHOOK_TOKEN", "").strip())
    # The price of one credit, in cents of a real; how many credits one purchase may be (the minimum keeps
    # Asaas's fixed Pix fee, R$1.99, a small part of the price); and the packs the Credits page offers, next to one where the person picks the number.
    credit_price_cents: int = field(default_factory=lambda: int(os.getenv("PDD_CREDIT_PRICE_CENTS", "150")))
    credit_min: int = field(default_factory=lambda: int(os.getenv("PDD_CREDIT_MIN", "10")))
    credit_max: int = field(default_factory=lambda: int(os.getenv("PDD_CREDIT_MAX", "1000")))
    credit_packs: list[int] = field(default_factory=lambda: [
        int(n) for n in os.getenv("PDD_CREDIT_PACKS", "10,30").split(",") if n.strip()])
    session_days: int = field(default_factory=lambda: int(os.getenv("PDD_SESSION_DAYS", "7")))
    frontend_dir: Path = field(default_factory=lambda: Path(os.getenv(
        "PDD_FRONTEND_DIR", Path(__file__).parent / "frontend" / "dist")))

    def __post_init__(self):
        if self.billing not in ("keys", "credits"):
            raise ValueError(f"PDD_BILLING must be 'keys' or 'credits', not {self.billing!r}")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "runs").mkdir(exist_ok=True)
        (self.data_dir / "templates").mkdir(exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'app.db'}"
        if not self.secret_key:
            # Kept in the data folder so logins survive restarts; set PDD_SECRET_KEY to manage it yourself.
            key_file = self.data_dir / "secret_key"
            if not key_file.exists():
                key_file.write_text(secrets.token_urlsafe(48))
                key_file.chmod(0o600)
            self.secret_key = key_file.read_text().strip()

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def uses_credits(self) -> bool:
        return self.billing == "credits"

    @property
    def payments_configured(self) -> bool:
        return bool(self.asaas_api_key and self.asaas_webhook_token)

    @property
    def asaas_url(self) -> str:
        return "https://api-sandbox.asaas.com/v3" if self.asaas_sandbox else "https://api.asaas.com/v3"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def translations_dir(self) -> Path:
        """Pidi's template translated by the model, one folder per language (see pidi_template.py)."""
        return self.data_dir / "pidi_templates"

    @property
    def templates_dir(self) -> Path:
        return self.data_dir / "templates"
