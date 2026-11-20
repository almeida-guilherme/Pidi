"""
Company PDD templates: uploaded once, analyzed once (what to keep, remove and fill; see template.py), then
chosen when uploading recordings. Each run gets its own copy of the template and its analysis, so deleting
or replacing a template never changes documents already made with it.
"""
import json
import os
import shutil
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlmodel import select

from web.auth import active_user, current_user
from web.db import Template, User, session
from web.keys import request_key

router = APIRouter(prefix="/api/templates", tags=["templates"])

TEMPLATE_FILE = "template.docx"
ANALYSIS_FILE = "template_analysis.json"
# The author's corrections of the analysis: per block index, what the analysis had decided, for undoing them.
EDITS_FILE = "template_edits.json"
FILL_ACTIONS = {"write", "table_rows", "table_answers"}
_editing = threading.Lock()  # one correction at a time: each rewrites the analysis file
MAX_TEMPLATE_MB = 25


class TemplateOut(BaseModel):
    id: str
    name: str
    language: str
    created_at: datetime


class TemplateBlock(BaseModel):
    index: int
    kind: str
    text: str
    action: str
    purpose: str
    reason: str
    editable: bool = False
    original: dict | None = None  # the analysis's own decision ({action, purpose}), when the author changed it


class BlockChange(BaseModel):
    action: Literal["keep", "remove", "write", "table_rows", "table_answers"]
    purpose: str = Field(default="", max_length=300)  # for the fill actions: what the part must contain


class TemplateDetail(TemplateOut):
    missing_label: str
    placeholders: list[dict]
    step_label: str
    blocks: list[TemplateBlock]


def default_analyze(path: str, ai: dict | None = None):
    """Analyzes a template with the person's AI provider (None: the server's OpenAI key)."""
    import providers
    from template import analyze_template
    return analyze_template(path, providers.writing_model(ai))


def _analyze_measured(request: Request, user: User, path: str, own_key: dict, name: str):
    """Reads the template and records what it cost (web/usage.py)."""
    from costs import measured
    from web.usage import add_measured
    with measured() as usage:
        try:
            return request.app.state.analyze_template(path, **own_key)
        finally:
            add_measured(request.app.state.engine, user.id, "template", own_key.get("ai"), usage.usage_metadata,
                         note=name)


def template_dir(request: Request, template: Template) -> Path:
    return request.app.state.settings.templates_dir / template.id


def own_template(request: Request, template_id: str, user: User) -> Template:
    with session(request.app.state.engine) as db:
        template = db.get(Template, template_id)
    if template is None or template.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found.")
    return template


def copy_into_run(request: Request, template: Template, run_dir: Path) -> None:
    """The run's own copy, which the pipeline finds in the run folder (pipeline.TEMPLATE_FILE)."""
    from pipeline import TEMPLATE_ANALYSIS_FILE, TEMPLATE_FILE as RUN_TEMPLATE_FILE
    source = template_dir(request, template)
    shutil.copyfile(source / TEMPLATE_FILE, run_dir / RUN_TEMPLATE_FILE)
    shutil.copyfile(source / ANALYSIS_FILE, run_dir / TEMPLATE_ANALYSIS_FILE)


@router.get("", response_model=list[TemplateOut])
def list_templates(request: Request, user: User = Depends(current_user)):
    with session(request.app.state.engine) as db:
        return db.exec(select(Template).where(Template.user_id == user.id).order_by(Template.created_at.desc())).all()


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TemplateDetail)
async def create_template(request: Request, file: UploadFile = File(...), user: User = Depends(active_user)):
    """Reads the template with the model (about half a minute). The page can cancel it: a template whose page
    stopped waiting isn't added."""
    name = os.path.basename(file.filename or "template.docx")
    if not name.lower().endswith(".docx"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload the template as a Word document (.docx).")
    # Analyzing a template is one model call: on the person's own key (billing "keys"), or free on the server's
    # (credits), so how often is limited instead.
    own_key = request_key(request, user)
    request.app.state.limits.template_user.take(str(user.id))
    template = Template(user_id=user.id, name=name, language="")
    folder = template_dir(request, template)
    folder.mkdir(parents=True)
    try:
        data = await file.read(MAX_TEMPLATE_MB * 1024 * 1024 + 1)
        if len(data) > MAX_TEMPLATE_MB * 1024 * 1024:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"The template is larger than {MAX_TEMPLATE_MB} MB.")
        path = folder / TEMPLATE_FILE
        path.write_bytes(data)
        if not zipfile.is_zipfile(path):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "The file could not be read as a Word document.")
        try:
            from docx import Document
            await run_in_threadpool(Document, str(path))
        except Exception:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "The file could not be read as a Word document.")
        analysis = await run_in_threadpool(_analyze_measured, request, user, str(path), own_key, name)
        if await request.is_disconnected():  # cancelled on the page while it was being read
            shutil.rmtree(folder, ignore_errors=True)
            return Response(status_code=499)
        (folder / ANALYSIS_FILE).write_text(json.dumps(analysis.model_dump(), ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    template.language = analysis.language
    with session(request.app.state.engine) as db:
        db.add(template)
        db.commit()
    return await run_in_threadpool(_detail, request, template)


def _load_analysis(folder: Path):
    from template import TemplateAnalysis
    return TemplateAnalysis.model_validate_json((folder / ANALYSIS_FILE).read_text(encoding="utf-8"))


def _load_edits(folder: Path) -> dict[str, dict]:
    path = folder / EDITS_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _fixed(analysis) -> set[int]:
    """Blocks the steps are written from (the example step, its group heading and details): not changed by hand."""
    fmt = analysis.step_format
    return {i for i in (fmt.example_step_index, fmt.example_group_heading_index, fmt.details_index) if i is not None}


def _editable(block: dict, fixed: set[int]) -> bool:
    return block["kind"] != "steps" and block["action"] not in ("steps", "diagram") and block["index"] not in fixed


def _detail(request: Request, template: Template) -> TemplateDetail:
    from template import summarize_analysis
    folder = template_dir(request, template)
    analysis = _load_analysis(folder)
    edits, fixed = _load_edits(folder), _fixed(analysis)
    blocks = [TemplateBlock(**b, editable=_editable(b, fixed),
                            original=edits.get(str(b["index"])) if b["kind"] != "steps" else None)
              for b in summarize_analysis(str(folder / TEMPLATE_FILE), analysis)]
    return TemplateDetail(**template.model_dump(include=set(TemplateOut.model_fields)),
                          missing_label=analysis.missing_label,
                          placeholders=[p.model_dump() for p in analysis.placeholders],
                          step_label=analysis.step_format.label.replace("{n}", "1") + analysis.step_format.separator,
                          blocks=blocks)


def _write(path: Path, text: str) -> None:
    """Written next to the file and moved over it, so a project starting meanwhile never reads half of it."""
    partial = path.with_suffix(path.suffix + ".new")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


@router.put("/{template_id}/blocks/{index}", response_model=TemplateDetail)
def change_block(template_id: str, index: int, change: BlockChange, request: Request,
                 user: User = Depends(current_user)):
    """Corrects what the analysis decided for one part of the template. Projects created afterwards use it;
    those made before keep their own copy."""
    from template import summarize_analysis
    template = own_template(request, template_id, user)
    folder = template_dir(request, template)
    with _editing:
        analysis = _load_analysis(folder)
        plan = next((b for b in analysis.blocks if b.index == index), None)
        block = next((b for b in summarize_analysis(str(folder / TEMPLATE_FILE), analysis)
                      if b["index"] == index and b["kind"] != "steps"), None)
        if plan is None or block is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "That part of the template doesn't exist.")
        if not _editable(block, _fixed(analysis)):
            raise HTTPException(status.HTTP_409_CONFLICT, "This part is where the steps or the diagram are written; "
                                                          "it can't be changed.")
        table = block["kind"] == "table"
        if change.action in ("table_rows", "table_answers") and not table or change.action == "write" and table:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "A table can be kept, removed or have its rows or answers filled; text can be kept, "
                                "removed or written for each process.")
        purpose = " ".join(change.purpose.split())
        if change.action in FILL_ACTIONS and not purpose:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                "Say what this part must contain for each process.")
        edits = _load_edits(folder)
        original = edits.get(str(index)) or {"action": plan.action, "purpose": plan.purpose, "reason": plan.reason}
        plan.action = change.action
        plan.purpose = purpose if change.action in FILL_ACTIONS else ""
        plan.reason = ""  # the analysis's reason was for its own decision
        if plan.action == original["action"] and (plan.action not in FILL_ACTIONS or plan.purpose == original["purpose"]):
            plan.purpose, plan.reason = original["purpose"], original.get("reason", "")
            edits.pop(str(index), None)  # back to the analysis's own decision
        else:
            edits[str(index)] = original
        _write(folder / ANALYSIS_FILE, analysis.model_dump_json(indent=2))
        _write(folder / EDITS_FILE, json.dumps(edits, ensure_ascii=False, indent=2))
    return _detail(request, template)


@router.get("/{template_id}", response_model=TemplateDetail)
def get_template(template_id: str, request: Request, user: User = Depends(current_user)):
    return _detail(request, own_template(request, template_id, user))


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, request: Request, user: User = Depends(current_user)):
    template = own_template(request, template_id, user)
    with session(request.app.state.engine) as db:
        db.delete(db.get(Template, template_id))
        db.commit()
    shutil.rmtree(template_dir(request, template), ignore_errors=True)
