import logging
import json
import os
import re
import shutil
import subprocess
import threading
from urllib.parse import quote as url_quote
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlmodel import select

from pidi_template import normalize_language
from assess import Assessment, load as load_assessment
from roi import RoiInputs, RoiResult, calculate, load_inputs, save_inputs
from web.auth import active_user, current_user
from transcribe import media_seconds
from web.credits import NotEnoughCredits, add_charged_run, quote, refund
from web.db import ApiKey, Run, Usage, User, session, utcnow
from web.keys import pick_key, request_key, server_key_available
from web.roi_templates import own_roi_template
from web.templates import copy_into_run, own_template

log = logging.getLogger("pdd.runs")

router = APIRouter(prefix="/api/runs", tags=["runs"])

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".wmv"}
FRAME_ID = re.compile(r"^F\d{3,4}$")
CHUNK = 1024 * 1024


class RunSummary(BaseModel):
    steps: int | None = None
    screenshots: int | None = None
    applications: list[str] = []
    recorded_seconds: float | None = None
    warnings: list[str] = []
    to_complete: int = 0  # parts of a template document highlighted for the author to fill in


class RoiOut(BaseModel):
    inputs: RoiInputs                     # as the author gave them (all None when they gave none)
    result: RoiResult
    recorded_seconds: float | None        # how long the process took in the recording, once it's processed
    assessment: Assessment | None = None  # whether it's worth automating, and the AI's figures (assess.py)


class RunOut(BaseModel):
    id: str
    video_name: str
    language: str | None
    status: str
    stage: str | None
    message: str | None
    error: str | None
    process_name: str | None
    name: str | None = None
    template_name: str | None = None
    credits: int | None = None            # what the project was charged
    refunded: bool = False                # its credits were given back
    cost_usd: float | None = None         # what it has cost on the AI provider so far, estimated (web/usage.py)
    # The stages this run goes through (pipeline.STAGES or TEMPLATE_STAGES), for the progress display.
    stages: list[str] = []
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # Set when the retention rule deleted the recording; the document and its screenshots remain.
    recording_deleted_at: datetime | None = None
    summary: RunSummary | None = None
    roi: RoiOut | None = None


def _run_dir(request: Request, run: Run) -> Path:
    return request.app.state.settings.runs_dir / run.id


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _steps(numbers: list[int]) -> str:
    """[2] -> "Step 2 has", [2, 5] -> "Steps 2 and 5 have"."""
    if len(numbers) == 1:
        return f"Step {numbers[0]} has"
    return f"Steps {', '.join(map(str, numbers[:-1]))} and {numbers[-1]} have"


def _summary(report: dict) -> RunSummary:
    warnings = []
    if report.get("steps_without_image"):
        warnings.append(f"{_steps(report['steps_without_image'])} no screenshot: nothing on screen clearly showed "
                        "the action. Add one by hand if it matters.")
    if report.get("steps_not_refined"):
        warnings.append(f"{_steps(report['steps_not_refined'])} a less specific description (the detailed pass "
                        "skipped them). Check their wording.")
    if not report.get("flowchart", True):
        warnings.append("The flowchart could not be drawn (Graphviz missing on the server).")
    if report.get("missing_marks"):
        warnings.append(f"{report['missing_marks']} part(s) of the document are highlighted in yellow: the recording "
                        "didn't say what goes there (e.g. contacts, versions, volumes). Fill them in before sharing it.")
    return RunSummary(steps=report.get("steps"), screenshots=report.get("images_inserted"),
                      applications=report.get("applications") or [],
                      recorded_seconds=report.get("recorded_seconds"), warnings=warnings,
                      to_complete=report.get("missing_marks") or 0)


def _out(request: Request, run: Run, with_summary: bool = False) -> RunOut:
    from pipeline import stages_for
    out = RunOut(**run.model_dump(include=set(RunOut.model_fields) - {"summary", "stages", "refunded", "roi", "cost_usd"}),
                 stages=stages_for(run.template_name is not None, run.language),
                 refunded=run.refunded_at is not None)
    if with_summary:
        run_dir = _run_dir(request, run)
        report = _read_json(run_dir / "report.json") or {}
        if run.status == "done":
            out.summary = _summary(report) if report else None
        out.roi = _roi(run_dir, report)
        with session(request.app.state.engine) as db:
            costs = db.exec(select(Usage.cost_usd).where(Usage.run_id == run.id)).all()
        out.cost_usd = round(sum(costs), 4) if costs else report.get("cost_usd")
    return out


def _roi(run_dir: Path, report: dict) -> RoiOut:
    inputs = load_inputs(str(run_dir)) or RoiInputs()
    recorded, assessment = report.get("recorded_seconds"), load_assessment(str(run_dir))
    return RoiOut(inputs=inputs, result=calculate(inputs, recorded, assessment), recorded_seconds=recorded,
                  assessment=assessment)


def _own_run(request: Request, run_id: str, user: User) -> Run:
    """The run, if it exists and belongs to `user`; otherwise 404 (not 403, so ids of others don't leak)."""
    with session(request.app.state.engine) as db:
        run = db.get(Run, run_id)
    if run is None or run.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run not found.")
    return run


def _has_video_stream(path: Path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    return result.returncode == 0 and bool(result.stdout.strip())


@router.get("", response_model=list[RunOut])
def list_runs(request: Request, user: User = Depends(current_user)):
    with session(request.app.state.engine) as db:
        runs = db.exec(select(Run).where(Run.user_id == user.id).order_by(Run.created_at.desc())).all()
    return [_out(request, run) for run in runs]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RunOut)
def create_run(request: Request, file: UploadFile = File(...), template_id: str | None = Form(None),
               name: str | None = Form(None), consent: bool = Form(False),
               language: str = Form("en"), roi_template_id: str | None = Form(None),
               api_key_id: str | None = Form(None), user: User = Depends(active_user)):
    settings = request.app.state.settings
    if not consent:
        # The recording shows screens and people and is sent to OpenAI; whoever uploads it confirms they may.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "Confirm that you're allowed to upload this recording.")
    template = own_template(request, template_id, user) if template_id else None
    roi_template = own_roi_template(request, roi_template_id, user) if roi_template_id else None
    if not template:
        try:
            language = normalize_language(language)
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    # With billing "keys", the person's own key it will run on (None: the server's, when allowed).
    key = None if settings.uses_credits else pick_key(request, user, api_key_id)
    video_name = os.path.basename(file.filename or "recording")
    ext = os.path.splitext(video_name)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            f"Upload a screen recording ({', '.join(sorted(VIDEO_EXTENSIONS))}).")

    run = Run(user_id=user.id, video_name=video_name, video_file=f"video{ext}", name=(name or "").strip()[:120] or None,
              template_name=template.name if template else None, language=None if template else language,
              api_key_id=key.id if key else None, consent_at=utcnow())
    run_dir = settings.runs_dir / run.id
    run_dir.mkdir(parents=True)
    limit, size = settings.max_upload_mb * CHUNK, 0
    try:
        with open(run_dir / run.video_file, "wb") as out:
            while chunk := file.file.read(CHUNK):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                                        f"The file is larger than {settings.max_upload_mb} MB.")
                out.write(chunk)
        if not _has_video_stream(run_dir / run.video_file):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "The file could not be read as a video.")
        if template:
            copy_into_run(request, template, run_dir)
        if roi_template:  # the project's own copy of the assumptions, which its author can change
            save_inputs(str(run_dir), RoiInputs(**json.loads(roi_template.assumptions), template_name=roi_template.name))
        if settings.uses_credits:
            # Charged before anything is spent on it, at a price set by the recording's length.
            credits = quote(settings, media_seconds(str(run_dir / run.video_file)), run.language)
            add_charged_run(request.app.state.engine, run, credits)
        else:  # runs on the person's own key, billed by OpenAI
            with session(request.app.state.engine) as db:
                db.add(run)
                db.commit()
    except NotEnoughCredits as exc:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(exc))
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise

    request.app.state.jobs.notify()
    return _out(request, run)


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: str, request: Request, user: User = Depends(current_user)):
    return _out(request, _own_run(request, run_id, user), with_summary=True)


@router.get("/{run_id}/model")
def get_model(run_id: str, request: Request, user: User = Depends(current_user)):
    """The process model for display: like process_model.json, with image URLs instead of server paths."""
    run = _own_run(request, run_id, user)
    model = _read_json(_run_dir(request, run) / "process_model.json")
    if model is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The process model isn't ready yet.")
    for step in model["as_is"]:
        path = step.pop("main_frame_path", None)
        step["image_url"] = f"/api/runs/{run.id}/frames/{Path(path).stem}" if path else None
    return model


@router.get("/{run_id}/frames/{frame_id}")
def get_frame(run_id: str, frame_id: str, request: Request, user: User = Depends(current_user)):
    run = _own_run(request, run_id, user)
    if not FRAME_ID.match(frame_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Frame not found.")
    path = _run_dir(request, run) / "frames" / f"{frame_id}.jpg"
    if not path.exists():
        # Runs made with --from-run point at the earlier run's frames; follow frames.json.
        frames = _read_json(_run_dir(request, run) / "frames.json") or []
        path = Path(next((f["path"] for f in frames if f["id"] == frame_id), ""))
        if not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Frame not found.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


def _document_name(run: Run) -> str:
    base = run.name or run.process_name or os.path.splitext(run.video_name)[0]
    base = re.sub(r"[^\w\s.-]", "", base).strip()[:80] or "Process"
    return f"{base} - PDD.docx"


@router.get("/{run_id}/document")
def get_document(run_id: str, request: Request, user: User = Depends(current_user)):
    run = _own_run(request, run_id, user)
    path = _run_dir(request, run) / "output" / "PDD_final.docx"
    if run.status != "done" or not path.exists():
        raise HTTPException(status.HTTP_409_CONFLICT, "The document isn't ready yet.")
    return FileResponse(path, filename=_document_name(run),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# One document is rewritten at a time: two saves of the same project's ROI would write the same file.
_rebuilding = threading.Lock()


@router.put("/{run_id}/roi", response_model=RunOut)
def set_roi(run_id: str, inputs: RoiInputs, request: Request, user: User = Depends(current_user)):
    """Saves the project's ROI figures (none: removes them). A finished project's document is written again with
    them, from its saved results, so it costs no credits; one still being made gets them when it's written."""
    run = _own_run(request, run_id, user)
    request.app.state.limits.roi_user.take(str(user.id))
    run_dir = str(_run_dir(request, run))
    with _rebuilding:
        save_inputs(run_dir, inputs)
        if run.status == "done":
            from pipeline import rebuild_document
            rebuild_document(run_dir)
    return _out(request, _own_run(request, run_id, user), with_summary=True)


@router.post("/{run_id}/assess", response_model=RunOut)
def assess_old_run(run_id: str, request: Request, user: User = Depends(current_user)):
    """The viability assessment of a project made before it existed: one model call (on the person's own key, or
    with credits on the server's, free), once per project. Its document is written again with it."""
    run = _own_run(request, run_id, user)
    run_dir = _run_dir(request, run)
    if run.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "A project is assessed when it's ready.")
    if load_assessment(str(run_dir)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This project is already assessed.")
    request.app.state.limits.roi_user.take(str(user.id))
    with _rebuilding:
        if load_assessment(str(run_dir)) is None:  # not assessed by a click a moment earlier
            from costs import measured
            from web.usage import add_measured
            own_key = request_key(request, user)
            try:
                with measured() as usage:
                    try:
                        request.app.state.assess_run(str(run_dir), **own_key)
                    finally:
                        add_measured(request.app.state.engine, user.id, "assess", own_key.get("ai"),
                                     usage.usage_metadata, run_id=run_id)
            except Exception:
                log.exception("assessing run %s failed", run_id)
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The assessment failed. Try again in a moment.")
    return _out(request, _own_run(request, run_id, user), with_summary=True)


@router.get("/{run_id}/automation")
def get_automation_spec(run_id: str, request: Request, user: User = Depends(current_user)):
    """The automation spec (spec.py): automation.md and its screenshots, as a zip, for an agent that builds the
    automation. Written from the run's saved results each time, so it follows ROI changes."""
    import spec
    run = _own_run(request, run_id, user)
    if run.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "The automation spec is ready when the document is.")
    data = spec.write_zip(str(_run_dir(request, run)), name=run.name or run.process_name)
    filename = _document_name(run).removesuffix(" - PDD.docx") + " - automation.zip"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{url_quote(filename)}"})


class SpecPreview(BaseModel):
    markdown: str                         # automation.md as it would be downloaded now
    screenshots: int                      # in the zip next to it
    open_questions: int


@router.get("/{run_id}/automation/preview", response_model=SpecPreview)
def preview_automation_spec(run_id: str, request: Request, user: User = Depends(current_user)):
    """automation.md on its own, to show on the project page."""
    import spec
    run = _own_run(request, run_id, user)
    if run.status != "done":
        raise HTTPException(status.HTTP_409_CONFLICT, "The automation spec is ready when the document is.")
    markdown, screenshots = spec.build(str(_run_dir(request, run)), name=run.name or run.process_name)
    questions = markdown.split("\n## Open questions\n", 1)[-1].count("\n- ")
    return SpecPreview(markdown=markdown, screenshots=len(screenshots), open_questions=questions)


@router.post("/{run_id}/retry", response_model=RunOut)
def retry_run(run_id: str, request: Request, user: User = Depends(current_user)):
    """Queues a failed run again (free, with credits); it continues from its last finished stage."""
    run = _own_run(request, run_id, user)
    if run.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed runs can be retried.")
    if run.recording_deleted_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "The recording was deleted (retention period); create the project again to retry.")
    if request.app.state.settings.uses_credits:
        if run.credits is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "This project was made before Pidi used credits. Create it again to run it.")
        if run.refunded_at is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "This project's credits were given back; create it again to run it.")
    elif run.api_key_id is None and not server_key_available(request.app.state.settings):
        raise HTTPException(status.HTTP_409_CONFLICT, "This project has no AI provider to run on. Connect one on the "
                                                      "Account page and create the project again.")
    elif run.api_key_id is not None:
        with session(request.app.state.engine) as db:
            if db.get(ApiKey, run.api_key_id) is None:
                raise HTTPException(status.HTTP_409_CONFLICT, "The API key chosen for this project was removed. "
                                                              "Create the project again with another key.")
    with session(request.app.state.engine) as db:
        run = db.get(Run, run_id)
        run.status, run.error, run.message, run.finished_at = "queued", None, "Waiting to retry", None
        db.add(run)
        db.commit()
    request.app.state.jobs.notify()
    return _out(request, run)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: str, request: Request, user: User = Depends(current_user)):
    run = _own_run(request, run_id, user)
    if run.status == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "The run is in progress; delete it when it has finished.")
    if run.started_at is None:  # never processed, so nothing was spent on it
        refund(request.app.state.engine, run.id, "deleted before it was processed")
    with session(request.app.state.engine) as db:
        db.delete(db.get(Run, run_id))
        db.commit()
    shutil.rmtree(_run_dir(request, run), ignore_errors=True)
