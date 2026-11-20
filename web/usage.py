"""
What people spend on their AI provider, estimated (list prices times tokens, costs.py), and the Costs page's API.

Every paid action adds a Usage row: each attempt at a project (jobs.py, from the run's report), each redo, each
assessment and each template read (measured around the call). Projects made before this was recorded get a row
from their report the first time the page is opened.
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlmodel import select

from costs import cost_by_model
from web.auth import current_user
from web.db import Run, Usage, User, session, utcnow

router = APIRouter(prefix="/api/costs", tags=["costs"])
KINDS = ("project", "redo", "assess", "template")


def add(engine, user_id: int, kind: str, provider: str | None, cost_usd: float, by_model: dict, tokens: int = 0,
        unpriced: list[str] | None = None, run_id: str | None = None, note: str | None = None) -> None:
    with session(engine) as db:
        db.add(Usage(user_id=user_id, run_id=run_id, kind=kind, provider=provider or "server",
                     cost_usd=round(cost_usd, 5), by_model=json.dumps(by_model), tokens=tokens,
                     unpriced=",".join(unpriced) if unpriced else None, note=note))
        db.commit()


def add_measured(engine, user_id: int, kind: str, ai: dict | None, usage_metadata: dict, run_id: str | None = None,
                 note: str | None = None) -> None:
    """A row for model calls measured with costs.measured()."""
    provider = (ai or {}).get("provider")
    by_model, unpriced = cost_by_model(usage_metadata, free=provider == "ollama")
    tokens = sum(u.get("total_tokens", 0) for u in usage_metadata.values())
    if usage_metadata:
        add(engine, user_id, kind, provider, sum(by_model.values()), by_model, tokens, unpriced, run_id, note)


def add_attempt(engine, run: Run, provider: str | None, report: dict | None) -> None:
    """A row for the attempt a run just made, from its report's "attempt" (pipeline._attempt_cost)."""
    attempt = (report or {}).get("attempt")
    if attempt:
        add(engine, run.user_id, "project", provider, attempt["cost_usd"], attempt["by_model"], attempt["tokens"],
            attempt.get("models_without_price"), run.id)


def _backfill(request: Request, user: User) -> None:
    """Projects that ran before costs were recorded: one row each, from the total in their report."""
    runs_dir: Path = request.app.state.settings.runs_dir
    with session(request.app.state.engine) as db:
        runs = db.exec(select(Run).where(Run.user_id == user.id, Run.started_at.is_not(None))).all()
        known = set(db.exec(select(Usage.run_id).where(Usage.user_id == user.id, Usage.kind == "project")).all())
        for run in runs:
            if run.id in known:
                continue
            try:
                report = json.loads((runs_dir / run.id / "report.json").read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError):
                report = {}
            by_model = {}
            if report.get("transcription_cost_usd"):
                by_model["whisper-1"] = report["transcription_cost_usd"]
            rest = (report.get("cost_usd") or 0) - by_model.get("whisper-1", 0)
            if rest > 0:  # per model it wasn't kept: split by tokens
                tokens = report.get("tokens_by_model") or {}
                total = sum(tokens.values()) or 1
                by_model.update({m: rest * n / total for m, n in tokens.items()} or {"unknown": rest})
            db.add(Usage(user_id=user.id, run_id=run.id, kind="project", provider=report.get("provider", "openai"),
                         cost_usd=round(report.get("cost_usd") or 0, 5), by_model=json.dumps(by_model),
                         tokens=report.get("tokens") or 0, created_at=run.finished_at or run.started_at or run.created_at,
                         note="from before costs were recorded"))
        db.commit()


class MonthOut(BaseModel):
    month: str                            # "2026-11"
    by_provider: dict[str, float]


class ProjectCost(BaseModel):
    run_id: str
    name: str
    created_at: datetime
    minutes: float | None
    providers: list[str]
    models: list[str]
    by_kind: dict[str, float]             # project, redo, assess
    total: float
    per_minute: float | None


class CostsOut(BaseModel):
    this_month: float
    last_month: float
    total: float
    months: list[MonthOut]                # the last twelve, oldest first
    by_model: dict[str, float]            # all time
    by_kind: dict[str, float]
    projects: list[ProjectCost]           # newest first; deleted projects are left out
    templates: float                      # reading templates, all time
    unpriced: list[str]                   # models whose cost isn't counted
    per_minute: float | None              # average cost per minute of video, over projects that ran once


def _month(d: datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"


@router.get("", response_model=CostsOut)
def costs(request: Request, user: User = Depends(current_user)):
    _backfill(request, user)
    with session(request.app.state.engine) as db:
        rows = db.exec(select(Usage).where(Usage.user_id == user.id).order_by(Usage.created_at)).all()
        runs = {r.id: r for r in db.exec(select(Run).where(Run.user_id == user.id)).all()}
    now = utcnow()
    this, last = _month(now), _month(datetime(now.year - (now.month == 1), (now.month - 2) % 12 + 1, 1))
    names = [_month(datetime(now.year - (now.month - 1 - i < 0), (now.month - 1 - i) % 12 + 1, 1)) for i in range(11, -1, -1)]
    months = {m: defaultdict(float) for m in names}
    by_model, by_kind, unpriced = defaultdict(float), defaultdict(float), set()
    per_run: dict[str, dict] = {}
    for row in rows:
        month = _month(row.created_at)
        if month in months:
            months[month][row.provider] += row.cost_usd
        for model, cost in json.loads(row.by_model or "{}").items():
            by_model[model] += cost
        by_kind[row.kind] += row.cost_usd
        unpriced |= set(filter(None, (row.unpriced or "").split(",")))
        if row.run_id and row.run_id in runs:
            entry = per_run.setdefault(row.run_id, {"by_kind": defaultdict(float), "providers": [], "models": []})
            entry["by_kind"][row.kind] += row.cost_usd
            if row.provider not in entry["providers"]:
                entry["providers"].append(row.provider)
            entry["models"] += [m for m in json.loads(row.by_model or "{}") if m not in entry["models"]]
    projects = []
    for run_id, entry in per_run.items():
        run = runs[run_id]
        report = _report(request, run_id)
        seconds = report.get("recorded_seconds") or (report.get("transcribed_minutes") or 0) * 60 or None
        total = sum(entry["by_kind"].values())
        projects.append(ProjectCost(
            run_id=run_id, name=run.name or run.process_name or run.video_name, created_at=run.created_at,
            minutes=round(seconds / 60, 1) if seconds else None, providers=entry["providers"], models=entry["models"],
            by_kind={k: round(v, 4) for k, v in entry["by_kind"].items()}, total=round(total, 4),
            per_minute=round(entry["by_kind"].get("project", 0) / (seconds / 60), 4) if seconds else None))
    projects.sort(key=lambda p: p.created_at, reverse=True)
    timed = [p for p in projects if p.minutes and p.by_kind.get("project")]
    per_minute = (sum(p.by_kind["project"] for p in timed) / sum(p.minutes for p in timed)) if timed else None
    total = sum(r.cost_usd for r in rows)
    return CostsOut(
        this_month=round(sum(months[this].values()), 4), last_month=round(sum(months[last].values()), 4),
        total=round(total, 4), months=[MonthOut(month=m, by_provider={p: round(c, 4) for p, c in months[m].items()})
                                       for m in names],
        by_model={m: round(c, 4) for m, c in sorted(by_model.items(), key=lambda kv: -kv[1])},
        by_kind={k: round(by_kind.get(k, 0), 4) for k in KINDS}, projects=projects,
        templates=round(by_kind.get("template", 0), 4), unpriced=sorted(unpriced),
        per_minute=round(per_minute, 4) if per_minute is not None else None)


def _report(request: Request, run_id: str) -> dict:
    try:
        return json.loads((request.app.state.settings.runs_dir / run_id / "report.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


# --- Estimate before uploading ---

# Tokens a minute of video takes, from real projects: describing the screenshots (most of it input: the images)
# and writing the steps, sections and assessment. With these, a project on OpenAI's defaults comes to about
# $0.10 a minute, as measured.
TYPICAL = {"screenshots": (95_000, 0.95), "writing": (17_000, 0.85)}  # tokens a minute, share that is input


class EstimateOut(BaseModel):
    low: float | None
    mid: float | None
    high: float | None
    basis: str                            # "history": your projects on these models; "typical"; "free"; "unknown"
    projects: int = 0                     # how many of your projects the history is from
    unpriced: list[str] = []


def _blended(model: str, input_share: float) -> float | None:
    from costs import price_for
    price = price_for(model)
    return None if price is None else (input_share * price[0] + (1 - input_share) * price[2]) / 1_000_000


@router.get("/estimate", response_model=EstimateOut)
def estimate(seconds: float, request: Request, api_key_id: str | None = None, user: User = Depends(current_user)):
    """What a recording of `seconds` will roughly cost on the key it will run on (the person's newest, or
    `api_key_id`; the server's OpenAI key when they have none)."""
    import providers
    from web.keys import pick_key
    minutes = max(seconds, 1) / 60
    key = None if request.app.state.settings.uses_credits else pick_key(request, user, api_key_id)
    provider = key.provider if key else "openai"
    caption, structure = providers.models_for({"provider": provider, "model": key.model if key else None})
    if provider == "ollama":
        return EstimateOut(low=0, mid=0, high=0, basis="free")

    # Your own projects on the same provider and models.
    per_minute, count = [], 0
    with session(request.app.state.engine) as db:
        rows = db.exec(select(Usage).where(Usage.user_id == user.id, Usage.kind == "project",
                                           Usage.provider == provider)).all()
    spent: dict[str, float] = defaultdict(float)
    for row in rows:
        spent[row.run_id] += row.cost_usd
    for run_id, cost in spent.items():
        report = _report(request, run_id) if run_id else {}
        run_seconds = report.get("recorded_seconds")
        same = {report.get("caption_model") or "", report.get("structure_model") or ""}
        if run_seconds and cost > 0 and all(any(m == x or m.startswith(x + "-") for m in same) for x in (caption, structure)):
            per_minute.append(cost / (run_seconds / 60))
    if per_minute:
        mid = sum(per_minute) / len(per_minute) * minutes
        return EstimateOut(low=round(mid * 0.7, 2), mid=round(mid, 2), high=round(mid * 1.3, 2), basis="history",
                           projects=len(per_minute))

    # A typical project, priced for these models.
    parts = [(caption, *TYPICAL["screenshots"]), (structure, *TYPICAL["writing"])]
    unpriced = [m for m, _, _ in parts if _blended(m, 0.9) is None]
    if unpriced:
        return EstimateOut(low=None, mid=None, high=None, basis="unknown", unpriced=sorted(set(unpriced)))
    mid = sum(tokens * _blended(model, share) for model, tokens, share in parts) * minutes
    if provider == "openai":
        from costs import WHISPER_PER_MINUTE
        mid += WHISPER_PER_MINUTE * minutes
    return EstimateOut(low=round(mid * 0.5, 2), mid=round(mid, 2), high=round(mid * 1.5, 2), basis="typical")
