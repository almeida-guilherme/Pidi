from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import event, inspect, text
from sqlmodel import Field, SQLModel, Session, create_engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    # Left from email-and-password log-in; accounts log in with Google now, and new ones have none.
    password_hash: str = ""
    # The Google account's id ("sub"); set on the first log-in with Google.
    google_sub: str | None = None
    name: str | None = None
    # Part of every session token; increasing it logs the account out everywhere.
    session_version: int | None = 0
    # When the privacy notice was accepted, and which version of it (see PRIVACY_VERSION in web/auth.py).
    privacy_accepted_at: datetime | None = None
    privacy_version: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Run(SQLModel, table=True):
    """One recording turned into a PDD. Its files live in data/runs/<id>/ (see pipeline.py for the layout)."""
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    video_name: str                       # the uploaded file's name, as the user knows it
    video_file: str                       # the saved file's name inside the run folder
    status: str = Field(default="queued", index=True)  # queued, running, done, failed
    stage: str | None = None              # pipeline stage (pipeline.STAGES) while running
    message: str | None = None            # latest progress message
    error: str | None = None
    process_name: str | None = None
    template_name: str | None = None      # the company template the document is written into, if any
    language: str | None = None           # of Pidi's template: a code or a language's name (pidi_template.py);
                                          # None with a company template
    name: str | None = None               # the project's name, as the user typed it
    api_key_id: str | None = None         # with billing "keys": the user's key it runs on; None: the server's
    credits: int | None = None            # what the project was charged when it was uploaded (web/credits.py)
    refunded_at: datetime | None = None   # when those credits were given back (no document came of it)
    consent_at: datetime | None = None    # when the uploader confirmed they may upload the recording
    recording_deleted_at: datetime | None = None  # when the retention rule deleted the recording
    created_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CreditEntry(SQLModel, table=True):
    """
    One change to a person's credits (web/credits.py): their balance is the sum of their entries. Entries are
    only ever added, so every balance has its history.
    """
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    amount: int                           # positive adds credits, negative takes them
    kind: str                             # grant, purchase, charge or refund
    run_id: str | None = None             # the project charged or refunded
    note: str | None = None               # shown in the history, e.g. the project's name or why credits were given
    created_at: datetime = Field(default_factory=utcnow)


class ApiKey(SQLModel, table=True):
    """A person's own AI provider key, with billing "keys" (web/keys.py). `secret` is encrypted with a key derived
    from the server's secret; `prefix` and `last4` are only for showing which key it is."""
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    provider: str                         # providers.PROVIDERS: openai, anthropic, gemini, ollama
    name: str
    prefix: str
    last4: str
    secret: str                           # empty, encrypted, for Ollama (no key)
    model: str | None = None              # the model to use instead of the provider's defaults
    created_at: datetime = Field(default_factory=utcnow)


class Usage(SQLModel, table=True):
    """What one paid action cost, estimated from its tokens and list prices (costs.py, web/usage.py): an attempt
    at a project, a redo, an assessment or reading a template. The Costs page adds them up."""
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    run_id: str | None = Field(default=None, index=True)   # None for a template
    kind: str                             # project, redo, assess, template
    provider: str                         # providers.PROVIDERS, or "server" for the server's key
    cost_usd: float = 0.0
    by_model: str = "{}"                  # JSON {model: cost}; "whisper-1" for OpenAI's transcription
    tokens: int = 0
    unpriced: str | None = None           # models with no known price, comma-separated: their cost isn't counted
    note: str | None = None               # e.g. the template's name
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Purchase(SQLModel, table=True):
    """Credits bought through Asaas's checkout (web/payments.py); they are added when Asaas says it was paid."""
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    credits: int
    price_cents: int                      # in reais
    status: str = "pending"               # pending, paid, canceled, expired
    checkout_id: str | None = Field(default=None, index=True)  # Asaas's
    created_at: datetime = Field(default_factory=utcnow)
    paid_at: datetime | None = None


class Template(SQLModel, table=True):
    """A company PDD template and its analysis, in data/templates/<id>/ (template.docx, template_analysis.json)."""
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str                             # the uploaded file's name
    language: str
    created_at: datetime = Field(default_factory=utcnow)


class RoiTemplate(SQLModel, table=True):
    """A company's ROI assumptions and indicators (roi.RoiAssumptions, as JSON), chosen for projects. Projects
    get a copy, so changing or deleting the template doesn't change them."""
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str
    assumptions: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


def _add_new_columns(engine) -> None:
    """
    create_all makes missing tables but doesn't change existing ones, so columns added to a model later
    (all nullable) are added here, keeping databases made by earlier versions working.
    """
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name not in existing and column.nullable:
                    kind = column.type.compile(engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {kind}'))


def make_engine(url: str):
    # Runs are processed in worker threads, so SQLite connections must be usable across threads.
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(conn, _):
            conn.execute("PRAGMA journal_mode=WAL")   # readers don't wait for the worker's progress writes
            conn.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine)
    _add_new_columns(engine)
    return engine


def session(engine) -> Session:
    return Session(engine, expire_on_commit=False)
