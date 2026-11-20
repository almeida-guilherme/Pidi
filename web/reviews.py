"""
Reviewing a finished project: its author approves the document, or says what's wrong with it, step by step and
in general, and has it done again with those notes (revise.py): a redo runs in the background (a minute or so),
keeps the version before it, and answers each note. Saved in the run folder as review.json; deleting the project
deletes it.
"""
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from web.auth import current_user
from web.db import User, utcnow

log = logging.getLogger("pdd.reviews")
router = APIRouter(prefix="/api/runs", tags=["review"])

REVIEW_FILE = "review.json"
_saving = threading.Lock()
# With credits, redos are free (the project was paid once), so a project gets this many.
CREDITS_REDOS = 2
# A redo still "running" after this long was cut off (e.g. the server restarted).
REDO_TIMEOUT = timedelta(minutes=15)

# What can be wrong with a step. Those marked as needing a note can't be acted on without one.
StepIssue = Literal["wording", "missing_after", "split", "merge_next", "screenshot", "name", "other"]
NEEDS_NOTE = {"missing_after", "other"}


class StepNote(BaseModel):
    number: int = Field(ge=1)
    issue: StepIssue
    note: str = Field(default="", max_length=600)


class Feedback(BaseModel):
    general: str = Field(default="", max_length=3000)
    steps: list[StepNote] = Field(default_factory=list, max_length=300)


class NoteResult(BaseModel):
    label: str                                         # "Step 3", "In general"
    outcome: Literal["fixed", "not_in_recording", "unclear"]
    explanation: str


class Version(BaseModel):
    """A redo: the version it made, the notes it was made from, and what came of each note."""
    number: int
    created_at: datetime
    notes: Feedback
    answers: list[NoteResult] = Field(default_factory=list)
    changed: list[int] = Field(default_factory=list)  # step numbers new or changed in this version


class ReviewOut(BaseModel):
    status: Literal["pending", "changes_requested", "redoing", "approved"] = "pending"
    feedback: Feedback = Field(default_factory=Feedback)
    updated_at: datetime | None = None
    approved_at: datetime | None = None
    version: int = 1                                   # the document's current version
    versions: list[Version] = Field(default_factory=list)  # the redos, oldest first
    redo_started_at: datetime | None = None
    redo_error: str | None = None
    redos: int = 0                                     # redos done, undone ones included
    redos_left: int | None = None                      # None: no limit


def load(run_dir) -> ReviewOut:
    try:
        with open(os.path.join(run_dir, REVIEW_FILE), encoding="utf-8") as f:
            review = ReviewOut.model_validate(json.load(f))
    except FileNotFoundError:
        return ReviewOut()
    started = review.redo_started_at
    if review.status == "redoing" and started and utcnow() - started.replace(tzinfo=started.tzinfo or timezone.utc) > REDO_TIMEOUT:
        review.status, review.redo_error = "changes_requested", "The redo was interrupted. Try it again."
    return review


def _out(request: Request, review: ReviewOut) -> ReviewOut:
    if request.app.state.settings.uses_credits:
        review.redos_left = max(CREDITS_REDOS - review.redos, 0)
    return review


def _save(run_dir, review: ReviewOut) -> None:
    path = os.path.join(run_dir, REVIEW_FILE)
    with open(path + ".new", "w", encoding="utf-8") as f:
        f.write(review.model_dump_json(indent=2))
    os.replace(path + ".new", path)


def _has_feedback(feedback: Feedback) -> bool:
    return bool(feedback.general.strip() or feedback.steps)


def _done_run(request: Request, run_id: str, user: User):
    from web.runs import _own_run, _run_dir
    run = _own_run(request, run_id, user)
    if run.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "A project is reviewed when it's ready.")
    return run, _run_dir(request, run)


@router.get("/{run_id}/review", response_model=ReviewOut)
def get_review(run_id: str, request: Request, user: User = Depends(current_user)):
    from web.runs import _own_run, _run_dir
    run = _own_run(request, run_id, user)
    return _out(request, load(_run_dir(request, run)))


@router.put("/{run_id}/review/feedback", response_model=ReviewOut)
def save_feedback(run_id: str, feedback: Feedback, request: Request, user: User = Depends(current_user)):
    """What's wrong with the document. Saving none takes the request for changes back."""
    _, run_dir = _done_run(request, run_id, user)
    with open(run_dir / "process_model.json", encoding="utf-8") as f:
        numbers = {s["number"] for s in json.load(f).get("as_is", [])}
    steps, seen = [], set()
    for item in feedback.steps:
        if item.number not in numbers:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"There's no step {item.number}.")
        if (item.number, item.issue) in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"Step {item.number} has the same problem twice; say it once.")
        note = " ".join(item.note.split())
        if item.issue in NEEDS_NOTE and not note:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"Step {item.number}: say what's missing or wrong.")
        seen.add((item.number, item.issue))
        steps.append(StepNote(number=item.number, issue=item.issue, note=note))
    feedback = Feedback(general=feedback.general.strip(), steps=sorted(steps, key=lambda s: s.number))
    with _saving:
        review = load(run_dir)
        if review.status == "approved":
            raise HTTPException(status.HTTP_409_CONFLICT, "This project is approved; undo the approval to ask for changes.")
        if review.status == "redoing":
            raise HTTPException(status.HTTP_409_CONFLICT, "The document is being done again; wait for it to finish.")
        review.feedback = feedback
        review.status = "changes_requested" if _has_feedback(feedback) else "pending"
        review.updated_at = utcnow()
        review.redo_error = None
        _save(run_dir, review)
    return _out(request, review)


@router.post("/{run_id}/review/approve", response_model=ReviewOut)
def approve(run_id: str, request: Request, user: User = Depends(current_user)):
    """The author says the document is right. Feedback given before is kept, for the record."""
    _, run_dir = _done_run(request, run_id, user)
    with _saving:
        review = load(run_dir)
        if review.status == "redoing":
            raise HTTPException(status.HTTP_409_CONFLICT, "The document is being done again; wait for it to finish.")
        review.status, review.approved_at = "approved", utcnow()
        review.updated_at = review.approved_at
        _save(run_dir, review)
    return _out(request, review)


@router.post("/{run_id}/review/reopen", response_model=ReviewOut)
def reopen(run_id: str, request: Request, user: User = Depends(current_user)):
    """Undoes the approval."""
    _, run_dir = _done_run(request, run_id, user)
    with _saving:
        review = load(run_dir)
        if review.status == "approved":
            review.status = "changes_requested" if _has_feedback(review.feedback) else "pending"
            review.approved_at, review.updated_at = None, utcnow()
            _save(run_dir, review)
    return _out(request, review)


@router.post("/{run_id}/review/redo", response_model=ReviewOut)
def start_redo(run_id: str, request: Request, user: User = Depends(current_user)):
    """Does the document again with the saved notes, in the background: the page polls the review until its
    status is no longer "redoing". One model call (on the person's own key, or with credits on the server's)."""
    from web.keys import request_key
    run, run_dir = _done_run(request, run_id, user)
    settings = request.app.state.settings
    own_key = request_key(request, user)
    with _saving:
        review = load(run_dir)
        if review.status != "changes_requested" or not _has_feedback(review.feedback):
            raise HTTPException(status.HTTP_409_CONFLICT, "Say what's wrong first: there are no notes to redo it with.")
        if settings.uses_credits and review.redos >= CREDITS_REDOS:
            raise HTTPException(status.HTTP_409_CONFLICT, f"A project can be done again {CREDITS_REDOS} times; "
                                                          "create it again for more.")
        request.app.state.limits.redo_user.take(str(user.id))
        review.status, review.redo_started_at, review.redo_error = "redoing", utcnow(), None
        _save(run_dir, review)
    threading.Thread(target=_redo, args=(request.app.state.redo_run, str(run_dir), review.feedback, review.version,
                                          own_key.get("ai"), run_id, request.app.state.engine, user.id),
                     daemon=True).start()
    return _out(request, review)


def _redo(redo_run, run_dir: str, feedback: Feedback, version: int, ai: dict | None, run_id: str, engine,
          user_id: int) -> None:
    from costs import measured
    from web.usage import add_measured
    try:
        with measured() as usage:
            try:
                result = redo_run(run_dir, feedback.model_dump(), version, ai)
            finally:
                add_measured(engine, user_id, "redo", ai, usage.usage_metadata, run_id=run_id)
    except Exception as exc:
        log.exception("redoing run %s failed", run_id)
        from web.jobs import _user_message
        with _saving:
            review = load(run_dir)
            review.status, review.redo_error = "changes_requested", _redo_message(exc, _user_message)
            _save(run_dir, review)
        return
    with _saving:
        review = load(run_dir)
        review.versions.append(Version(number=version + 1, created_at=utcnow(), notes=feedback,
                                       answers=[NoteResult(**a) for a in result["answers"]],
                                       changed=result["changed"]))
        review.version, review.feedback, review.redos = version + 1, Feedback(), review.redos + 1
        review.status, review.redo_error, review.updated_at = "pending", None, utcnow()
        _save(run_dir, review)
    log.info("run %s redone as version %s (steps changed: %s)", run_id, version + 1, result["changed"])


def _redo_message(exc: Exception, user_message) -> str:
    text = user_message(exc, own_key=True)
    return text.replace("Retry it; it continues from the last finished stage.", "Try the redo again.") \
               .replace("then create the project again", "then try the redo again") \
               .replace("then retry (the run continues from where it stopped)", "then try the redo again")


@router.post("/{run_id}/review/undo", response_model=ReviewOut)
def undo_redo(run_id: str, request: Request, user: User = Depends(current_user)):
    """Puts back the version before the last redo, with the notes it was made from."""
    import revise
    _, run_dir = _done_run(request, run_id, user)
    with _saving:
        review = load(run_dir)
        if not review.versions or review.status in ("redoing", "approved"):
            raise HTTPException(status.HTTP_409_CONFLICT, "There's no redo to undo.")
        last = review.versions.pop()
        revise.restore_version(str(run_dir), review.version - 1)
        review.version -= 1
        review.feedback, review.status, review.updated_at = last.notes, "changes_requested", utcnow()
        _save(run_dir, review)
    return _out(request, review)
