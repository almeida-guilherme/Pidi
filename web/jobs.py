"""
Runs the pipeline in background threads, one run at a time per worker.

The queue is the database itself (runs with status "queued"), so nothing else needs to be installed.
Each run's stages save their results in its folder (see pipeline.py), so a run that was interrupted by a
restart is simply queued again and continues where it stopped. Workers take turns between people: someone
with `runs_per_user` runs in progress waits, so a long queue of one person's projects doesn't hold up others.

A housekeeping thread applies the retention rule: `retention_days` after a run finished, its recording
is deleted (see delete_recording).
"""
import json
import logging
import shutil
import threading
import traceback
from datetime import timedelta
from pathlib import Path
from typing import Callable
from sqlalchemy import func, update
from sqlmodel import select

from costs import CostLimitReached
from web.credits import refund
from web.db import ApiKey, Run, User, session, utcnow

log = logging.getLogger("pdd.jobs")

# run_fn(video_path, run_dir, progress, recording_name=..., language=..., translations_dir=..., max_cost_usd=...,
# ai=...) -> (document path, report); progress(stage, message, done); recording_name is the uploaded file's
# name (the video is stored as video.<ext>); language is the language of Pidi's template (a company template in
# the run folder is used in its own), translated into translations_dir when it isn't shipped; max_cost_usd is
# what the run's model calls may cost at most (with credits, see web/credits.py); ai is the person's own provider
# and key (with billing "keys", providers.py, web/keys.py); without it the run uses the server's OPENAI_API_KEY.
RunFn = Callable[..., tuple[str, dict]]


def default_run_fn(video_path: str, run_dir: str, progress, recording_name: str | None = None,
                   language: str = "en", translations_dir: str | None = None,
                   max_cost_usd: float | None = None, ai: dict | None = None) -> tuple[str, dict]:
    from pipeline import run_pdd_pipeline  # imported here so the API starts fast and tests can swap it
    return run_pdd_pipeline(video_path, run_dir, progress=progress, recording_name=recording_name,
                            language=language, translations_dir=translations_dir, max_cost_usd=max_cost_usd, ai=ai)


HOUSEKEEPING_SECONDS = 3600


class JobRunner:
    def __init__(self, engine, runs_dir: Path, run_fn: RunFn = default_run_fn, workers: int = 1,
                 runs_per_user: int = 1, retention_days: int = 0, alerts=None, translations_dir: Path | None = None,
                 max_cost_per_credit_usd: float | None = None, billing: str = "credits", secret_key: str = "",
                 allow_server_key: bool = False):
        self.engine, self.runs_dir, self.run_fn, self.workers = engine, runs_dir, run_fn, workers
        self.runs_per_user, self.retention_days = runs_per_user, retention_days
        self.alerts = alerts  # web.mail.Alerts, told about failed runs
        self.translations_dir = translations_dir  # where Pidi's template in other languages is cached
        # A run's model calls may cost at most this per credit it was charged (None: no limit).
        self.max_cost_per_credit_usd = max_cost_per_credit_usd
        # "credits": runs are paid with credits, on the server's key; "keys": each runs on its owner's key, which
        # is read with secret_key; runs without one may use the server's key only while allow_server_key.
        self.billing, self.secret_key, self.allow_server_key = billing, secret_key, allow_server_key
        self._wake = threading.Condition()
        self._claim = threading.Lock()
        self._stop = False
        self._stopped = threading.Event()  # for the housekeeping thread, which doesn't share the workers' wake-ups
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        with session(self.engine) as db:
            # Runs that were in progress when the server stopped continue from their saved stages.
            for run in db.exec(select(Run).where(Run.status == "running")).all():
                run.status, run.message = "queued", "Resuming after a server restart"
                db.add(run)
            db.commit()
        for n in range(self.workers):
            thread = threading.Thread(target=self._loop, name=f"pdd-worker-{n}", daemon=True)
            thread.start()
            self._threads.append(thread)
        if self.retention_days > 0:
            threading.Thread(target=self._housekeeping, name="pdd-housekeeping", daemon=True).start()

    def alive_workers(self) -> int:
        return sum(1 for t in self._threads if t.is_alive())

    def stop(self) -> None:
        with self._wake:
            self._stop = True
            self._wake.notify_all()
        self._stopped.set()

    def notify(self) -> None:
        """Called after a run is queued, so an idle worker picks it up at once."""
        with self._wake:
            self._wake.notify()

    def _next_run(self) -> Run | None:
        """Claims the oldest queued run of someone below their limit of runs in progress. The conditional
        update makes the claim safe even if several server processes share the database: only one of them
        changes the status from "queued"."""
        with self._claim, session(self.engine) as db:
            busy = {user for user, n in db.exec(select(Run.user_id, func.count()).where(Run.status == "running")
                                                .group_by(Run.user_id)).all() if n >= self.runs_per_user}
            queued = select(Run).where(Run.status == "queued").order_by(Run.created_at)
            if busy:
                queued = queued.where(Run.user_id.not_in(busy))
            for run in db.exec(queued.limit(5)).all():
                claimed = db.exec(update(Run).where(Run.id == run.id, Run.status == "queued")
                                  .values(status="running", started_at=utcnow(), error=None))
                db.commit()
                if claimed.rowcount == 1:
                    return db.get(Run, run.id, populate_existing=True)
            return None

    def _loop(self) -> None:
        while True:
            with self._wake:
                if self._stop:
                    return
            run = self._next_run()
            if run is None:
                with self._wake:
                    if not self._stop:
                        self._wake.wait(timeout=5)
                continue
            self.process(run)

    def _update(self, run_id: str, **fields) -> None:
        with session(self.engine) as db:
            run = db.get(Run, run_id)
            if run is None:  # deleted meanwhile
                return
            for key, value in fields.items():
                setattr(run, key, value)
            db.add(run)
            db.commit()

    def process(self, run: Run) -> None:
        run_dir = self.runs_dir / run.id

        def progress(stage: str, message: str, done: bool) -> None:
            self._update(run.id, stage=stage, message=message)

        log.info("run %s started", run.id)
        limit = self.max_cost_per_credit_usd
        try:
            if self.billing == "credits":
                if run.credits is None:
                    raise Unpaid("This project was made before Pidi used credits. Create it again to run it.")
                paying = {"max_cost_usd": run.credits * limit if limit else None}
            else:
                ai = _key_for(self.engine, self.secret_key, run, self.allow_server_key)
                paying = {"ai": ai} if ai else {}  # nothing: the server's OpenAI key
            try:
                _, report = self.run_fn(str(run_dir / run.video_file), str(run_dir), progress,
                                        recording_name=run.video_name, language=run.language or "en",
                                        translations_dir=str(self.translations_dir) if self.translations_dir else None,
                                        **paying)
            finally:  # what this attempt cost, finished or not (web/usage.py)
                self._record(run, (paying.get("ai") or {}).get("provider"))
            roi, document = run_dir / "roi.json", run_dir / "output" / "PDD_final.docx"
            if roi.exists() and document.exists() and roi.stat().st_mtime > document.stat().st_mtime:
                from pipeline import rebuild_document  # ROI figures saved while the document was being written
                rebuild_document(str(run_dir))
            model = json.loads((run_dir / "process_model.json").read_text(encoding="utf-8"))
            self._update(run.id, status="done", finished_at=utcnow(), message="Document ready",
                         process_name=model.get("process_name"))
            log.info("run %s done (%s steps, $%s)", run.id, report.get("steps"), report.get("cost_usd"))
        except Exception as exc:  # any failure is shown to the user; details go to the server log
            details = traceback.format_exc()
            log.error("run %s failed:\n%s", run.id, details)
            message = _user_message(exc, own_key=self.billing == "keys" and run.api_key_id is not None)
            if isinstance(exc, CostLimitReached):
                # Pidi's price didn't cover this recording: the person gets their credits back.
                refund(self.engine, run.id, "stopped at its cost limit")
                message = (f"This recording needed much more processing than its price covers, so it was stopped "
                           f"and your {run.credits} credits were given back. Contact the site's administrator.")
            self._update(run.id, status="failed", finished_at=utcnow(), error=message)
            if self.alerts:
                self._alert(run, exc, message, details)
        finally:
            with self._wake:  # a slot of this person freed up: their next run may start
                self._wake.notify_all()

    def _record(self, run: Run, provider: str | None) -> None:
        from web.usage import add_attempt
        try:
            report = json.loads((self.runs_dir / run.id / "report.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return
        try:
            add_attempt(self.engine, run, provider, report)
        except Exception:  # the cost record never fails a run
            log.exception("recording the cost of run %s failed", run.id)

    def _alert(self, run: Run, exc: Exception, message: str, details: str) -> None:
        with session(self.engine) as db:
            user = db.get(User, run.user_id)
        self.alerts.report(f"run:{type(exc).__name__}", f"A project failed: {type(exc).__name__}", (
            f"Project {run.id} ({run.name or run.video_name}) of {user.email if user else 'a deleted account'} "
            f"failed at stage {run.stage or '?'}.\n\nShown to the user: {message}\n\n"
            f"{details[-4000:]}\n(Alerts of the same kind are paused for a while after this one.)\n"))

    # --- Retention ---

    def _housekeeping(self) -> None:
        while True:
            try:
                self.apply_retention()
            except Exception:
                log.exception("retention sweep failed")
            if self._stopped.wait(HOUSEKEEPING_SECONDS):
                return

    def apply_retention(self) -> int:
        """Deletes the recordings of runs finished more than retention_days ago; returns how many."""
        cutoff = utcnow() - timedelta(days=self.retention_days)
        with session(self.engine) as db:
            due = db.exec(select(Run).where(Run.status.in_(("done", "failed")), Run.recording_deleted_at.is_(None),
                                            Run.finished_at < cutoff)).all()
        for run in due:
            delete_recording(self.runs_dir / run.id, run.video_file)
            self._update(run.id, recording_deleted_at=utcnow())
            log.info("run %s: recording deleted (finished over %s days ago)", run.id, self.retention_days)
        return len(due)


def delete_recording(run_dir: Path, video_file: str) -> None:
    """
    The recording goes: the video, its audio and every frame the document doesn't use. What the document
    is made of stays (the document, its screenshots, the process model and the text files the stages saved),
    so the project can still be opened and its document downloaded.
    """
    (run_dir / video_file).unlink(missing_ok=True)
    shutil.rmtree(run_dir / "audio", ignore_errors=True)
    try:
        model = json.loads((run_dir / "process_model.json").read_text(encoding="utf-8"))
        used = {Path(s["main_frame_path"]).name for s in model.get("as_is", []) if s.get("main_frame_path")}
    except (FileNotFoundError, ValueError):
        used = set()
    for frame in (run_dir / "frames").glob("*.jpg"):
        if frame.name not in used:
            frame.unlink()


class Unpaid(Exception):
    pass


class MissingKey(Exception):
    pass


def _key_for(engine, secret_key: str, run: Run, allow_server_key: bool) -> dict | None:
    """With billing "keys": the run's own provider and key, decrypted (web.keys.ai_for); None for the server's key."""
    from web.keys import ai_for
    if run.api_key_id is None:
        if not allow_server_key:
            raise MissingKey("This project has no AI provider to run on. Connect one on the Account page "
                             "and create the project again.")
        return None
    with session(engine) as db:
        key = db.get(ApiKey, run.api_key_id)
    if key is None:
        raise MissingKey("The API key chosen for this project was removed. Create the project again with another key.")
    try:
        return ai_for(secret_key, key)
    except ValueError as exc:
        raise MissingKey(str(exc))


def _user_message(exc: Exception, own_key: bool = False) -> str:
    """A message the person who uploaded the video can act on, without internal details. `own_key`: the run
    used the person's own API key, which they can fix themselves."""
    name = type(exc).__name__
    text = str(exc)
    if isinstance(exc, (Unpaid, MissingKey)):
        return text
    if "AuthenticationError" in name or "api_key" in text.lower() or "API key not valid" in text:
        if own_key:
            return ("Your AI provider refused the API key. Check it on the Account page (remove it and connect a new "
                    "one), then create the project again.")
        return "The server's OpenAI key was refused. Retry later, or tell the site's administrator."
    if "RateLimit" in name or "insufficient_quota" in text or "credit balance" in text or "RESOURCE_EXHAUSTED" in text:
        if own_key:
            return ("Your AI provider account hit its rate limit or ran out of credit. Add credit there, then retry "
                    "(the run continues from where it stopped).")
        return "The server's OpenAI account is busy or ran out of credit. Retry later (the run continues from where it stopped)."
    if "NotFound" in name or "model_not_found" in text or ("not found" in text.lower() and "model" in text.lower()):
        return ("The AI model wasn't found. Check the model name on the Account page (or leave it empty for the "
                "default), then retry.")
    if "ConnectError" in name or "Connection refused" in text:
        return "The AI provider couldn't be reached. Retry later (the run continues from where it stopped)."
    if "Could not open video" in text or "ffprobe failed" in text:
        return "The file could not be read as a video."
    return f"The run failed ({name}). Retry it; it continues from the last finished stage."
