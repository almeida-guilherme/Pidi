"""
Doing a finished run's steps again from its author's review notes (web/reviews.py), without the video: the model
gets the whole step list, the notes, the detailed screen observations and narration around the steps the notes
point at, and those steps' screenshots, and answers with changes to the steps it was asked about (reword, split,
merge, add a missing step, pick another screenshot, fix a name). Code applies them, so the steps nobody marked stay
exactly as they were, then numbers and times the steps again and writes the document again (no other model call).

The version before is kept in versions/<n>/ (process model and document), so a redo can be undone. Each note gets
an answer: fixed, not in the recording (nothing the recording shows supports it; the model doesn't invent it), or
unclear.
"""
import base64
import io
import json
import logging
import os
import shutil
from typing import Literal, cast
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from schemas import ProcessModel, Step, format_timestamp, load_process_model, repair_model
from structure import (_key_names_text, _observations_text, _transcript_text, apply_key_names, enforce_chronology,
                       measure_durations)

VERSIONS_DIR = "versions"
# Screenshots sent with a redo: those of the marked steps first; enough to see them, few enough to stay cheap.
MAX_IMAGES = 8
# Observations around a marked step: from the step before it to the step after it, at most this many frames.
MAX_REGION_FRAMES = 40
MAX_NARRATION_CHARS = 30_000

log = logging.getLogger("pdd.revise")

ISSUES = {
    "wording": "the wording is wrong",
    "missing_after": "a step is missing right after this one",
    "split": "this should be two (or more) steps",
    "merge_next": "this and the next step are the same step",
    "screenshot": "the screenshot doesn't show this step",
    "name": "a name, field or system in it is wrong",
    "other": "something else is wrong",
}


class RevisedStep(BaseModel):
    instruction: str = Field(description="The step's instruction, in the same directive style and language as the others")
    short_label: str = Field(description="A very short label (max 4-5 words) for the flowchart")
    application: str = Field(default="", description="The application or website, spelled as in the other steps; empty if unclear")
    decision: str = Field(default="", description="The condition and what happens in each case, if the step depends on one; otherwise empty")
    frame_ids: list[str] = Field(default_factory=list, max_length=40, description="IDs of the observations that show this step, in chronological order, only from the observations given")
    main_frame_id: str | None = Field(default=None, description="The one id from frame_ids used as the step's screenshot: the frame that best shows the step done")


class StepChange(BaseModel):
    action: Literal["replace", "insert_after", "delete"] = Field(
        description="replace: step `step` becomes new_steps (one step to reword it, two or more to split it); "
                    "insert_after: new_steps are added right after step `step`; delete: step `step` is removed "
                    "(e.g. merged into the step before, which is replaced)")
    step: int = Field(description="The current number of the step changed")
    new_steps: list[RevisedStep] = Field(default_factory=list, max_length=10)


class NoteAnswer(BaseModel):
    note: int = Field(description="The note's number, as given")
    outcome: Literal["fixed", "not_in_recording", "unclear"] = Field(
        description="fixed: the steps were changed as the note asks; not_in_recording: the recording doesn't show or "
                    "say what the note needs, so nothing was invented; unclear: the note couldn't be understood")
    explanation: str = Field(description="One short sentence for the author: what changed, or why nothing did")


class Revision(BaseModel):
    changes: list[StepChange] = Field(default_factory=list, max_length=60, description="Only the changes the notes ask for")
    answers: list[NoteAnswer] = Field(default_factory=list, max_length=60, description="One answer per note")


SYSTEM_PROMPT = """You correct a Process Design Document (PDD) written from a screen recording, following its author's review notes. The document's steps are listed, then the author's notes, then the screen observations and narration around the steps the notes are about, and screenshots of those steps.

Rules:
- Change only what the notes ask for. Every step no note is about stays exactly as it is: don't reword, merge or reorder it.
- Notes that only reshape existing steps need no new evidence; the author knows the process. Do them: remove a step that isn't really a step (e.g. a title or a test line), split a step into the actions it already describes, merge two steps, reword a step more clearly.
- Notes that ask to ADD something (a missing step, a field, a button, a value, an approval) need the observations, narration or screenshots to show or say it. If they don't, make no change for that note and answer not_in_recording. Never invent screens, fields, values or steps.
- If the recording clearly contradicts a note, make no change and say so in its answer.
- Wording: rewrite the instruction to be accurate and specific (names, fields, buttons, values as shown). Missing step: insert_after with the missing step(s). Two steps: replace with two or more steps, each with its own frames. Same as the next: replace this step with the merged one and delete the next. Wrong screenshot: replace with the same text and another main_frame_id from the step's frames or the frames around it. Wrong name: fix it in the instruction, label and application.
- New and changed steps use only observation ids given here, in chronological order, and are written in the same style and language as the other steps.
- To remove a step, use delete with its number.
- Step numbers in changes are the current numbers, as listed. Give one answer per note, its explanation one short sentence in the language the author's notes are written in."""


def _image(path: str, max_width: int = 1366) -> str | None:
    """A screenshot as base64 JPEG, smaller if wide; None if it's gone (e.g. deleted by the retention rule)."""
    from PIL import Image
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            if img.width > max_width:
                img = img.resize((max_width, round(img.height * max_width / img.width)))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=80)
            return base64.b64encode(buffer.getvalue()).decode()
    except (FileNotFoundError, OSError):
        return None


def _frame_path(run_dir: str, frame: dict) -> str:
    path = frame.get("path") or ""
    return path if os.path.isfile(path) else os.path.join(run_dir, "frames", os.path.basename(path))


def _notes(feedback: dict) -> list[tuple[int | None, str]]:
    """The notes, numbered from 1: (step number or None for the general note, text for the model)."""
    out = []
    if (feedback.get("general") or "").strip():
        out.append((None, f"About the whole document: {feedback['general'].strip()}"))
    for n in feedback.get("steps", []):
        text = f"Step {n['number']}: {ISSUES.get(n['issue'], n['issue'])}"
        out.append((n["number"], text + (f" — the author says: \"{n['note']}\"" if n.get("note") else "")))
    return out


def _region(model: ProcessModel, numbers: set[int], position: dict[str, int], captions: list[dict]) -> list[dict]:
    """The observations around the marked steps: from the step before each to the step after it."""
    picked: set[int] = set()
    by_number = {s.number: s for s in model.as_is}
    for n in numbers:
        around = [by_number[k] for k in (n - 1, n, n + 1, n + 2) if k in by_number]
        idx = [position[f] for s in around for f in s.frame_ids if f in position]
        if not idx:
            continue
        lo, hi = min(idx), max(idx)
        if hi - lo + 1 > MAX_REGION_FRAMES:  # a long stretch: frames spread evenly over it
            step = (hi - lo) / (MAX_REGION_FRAMES - 1)
            picked |= {round(lo + k * step) for k in range(MAX_REGION_FRAMES)}
        else:
            picked |= set(range(lo, hi + 1))
    return [captions[i] for i in sorted(picked)]


def revise(run_dir: str, feedback: dict, llm, language: str = "English") -> tuple[ProcessModel, Revision, list[int]]:
    """The run's process model with the notes applied (not saved), the model's answer, and the numbers of the
    steps that are new or changed in the result."""
    model = load_process_model(json.load(open(os.path.join(run_dir, "process_model.json"), encoding="utf-8")))
    captions = json.load(open(os.path.join(run_dir, "captions.json"), encoding="utf-8"))
    frames = json.load(open(os.path.join(run_dir, "frames.json"), encoding="utf-8"))
    try:
        transcript = json.load(open(os.path.join(run_dir, "transcript.json"), encoding="utf-8")) or []
    except FileNotFoundError:
        transcript = []
    frame_by_id = {f["id"]: f for f in frames}
    position = {c["id"]: i for i, c in enumerate(captions)}
    notes = _notes(feedback)
    marked = {n for n, _ in notes if n is not None}
    general = any(n is None for n, _ in notes)

    steps_text = "\n".join(
        f"{s.number}. [{s.application or '?'}"
        f"{', frames ' + s.frame_ids[0] + '–' + s.frame_ids[-1] if s.frame_ids else ', no frames'}] {s.instruction}"
        + (f"  | Decision: {s.decision}" if s.decision else "")
        for s in model.as_is)
    notes_text = "\n".join(f"Note {i + 1}. {text}" for i, (_, text) in enumerate(notes))
    region = _region(model, marked, position, captions)
    if general:  # a note about the whole document: the whole recording, compact
        observations = _observations_text(captions, compact=True)
        narration = transcript
    else:
        observations = _observations_text(region)
        if region:
            start, end = region[0]["timestamp"], region[-1]["timestamp"]
            narration = [t for t in transcript if t["end"] >= start - 30 and t["start"] <= end + 5]
        else:
            narration = []
    narration_text = _transcript_text(narration)[:MAX_NARRATION_CHARS]

    content: list = [{"type": "text", "text": (
        f"Process: {model.process_name}\nThe document is written in {language}.\n\n{_key_names_text(model.key_names)}"
        f"Current steps:\n{steps_text}\n\nThe author's notes:\n{notes_text}\n\n"
        f"Screen observations{' (the whole recording)' if general else ' around the marked steps'}:\n\n{observations}\n\n"
        f"Audio narration for this part:\n\n{narration_text}")}]
    images = 0
    by_number = {s.number: s for s in model.as_is}
    for n in sorted(marked):
        step = by_number.get(n)
        if not step or images >= MAX_IMAGES:
            continue
        ids = [f for f in [os.path.splitext(os.path.basename(step.main_frame_path or ""))[0], *step.frame_ids[:1]]
               if f in frame_by_id]
        for frame_id in dict.fromkeys(ids):
            data = _image(_frame_path(run_dir, frame_by_id[frame_id]))
            if data and images < MAX_IMAGES:
                content += [{"type": "text", "text": f"Screenshot {frame_id} (step {n}):"},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}]
                images += 1

    result = repair_model(cast(Revision, llm.with_structured_output(Revision).invoke([
        SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=content)])))
    revised, changed = apply(model, result, frame_by_id, position)
    return revised, result, changed


def apply(model: ProcessModel, revision: Revision, frame_by_id: dict[str, dict],
          position: dict[str, int]) -> tuple[ProcessModel, list[int]]:
    """The steps with the changes applied, numbered and timed again; and the new numbers of new or changed steps.
    Changes to steps that don't exist are ignored; a step gets at most one replace or delete (the first)."""
    numbers = {s.number for s in model.as_is}
    replaced: dict[int, StepChange] = {}
    inserted: dict[int, list] = {}
    for change in revision.changes:
        if change.step not in numbers:
            continue
        if change.action == "insert_after":
            if change.new_steps:
                inserted.setdefault(change.step, []).extend(change.new_steps)
        elif change.step not in replaced and (change.action == "delete" or change.new_steps):
            replaced[change.step] = change

    def new(r: RevisedStep) -> Step:
        ids = sorted({f.strip().upper() for f in r.frame_ids if f.strip().upper() in position}, key=position.get)
        main = (r.main_frame_id or "").strip().upper()
        main = main if main in ids else (ids[-1] if ids else None)
        return Step(number=0, time="", instruction=r.instruction.strip(), short_label=r.short_label.strip(),
                    application=r.application.strip(), decision=r.decision.strip(), frame_ids=ids,
                    main_frame_path=frame_by_id[main]["path"] if main and main in frame_by_id else None)

    steps: list[tuple[Step, bool]] = []  # each step, and whether it's new or changed
    for step in model.as_is:
        change = replaced.get(step.number)
        if change is None:
            steps.append((step.model_copy(deep=True), False))
        elif change.action == "replace":
            made = [new(r) for r in change.new_steps]
            if len(made) == 1 and not made[0].frame_ids:  # reworded without its frames: it keeps them
                made[0].frame_ids, made[0].main_frame_path = list(step.frame_ids), step.main_frame_path
            steps += [(m, True) for m in made]
        steps += [(new(r), True) for r in inserted.get(step.number, [])]

    out = [s for s, _ in steps]
    for i, step in enumerate(out):
        step.number = i + 1
        step.start_seconds = step.duration_seconds = None
        step.time = format_timestamp(frame_by_id[step.frame_ids[0]]["timestamp"]) if step.frame_ids else ""
    enforce_chronology(out, frame_by_id)
    measure_durations(out, frame_by_id)
    revised = model.model_copy(update={"as_is": out})
    apply_key_names(revised)
    return revised, [s.number for s, changed in steps if changed]


# --- Versions ---

def _version_files(run_dir: str) -> dict[str, str]:
    """What a version holds: {name in the version folder: path in the run}."""
    return {"process_model.json": os.path.join(run_dir, "process_model.json"),
            "PDD_final.docx": os.path.join(run_dir, "output", "PDD_final.docx"),
            "flowchart.png": os.path.join(run_dir, "output", "flowchart.png"),
            "assessment.json": os.path.join(run_dir, "assessment.json")}


def save_version(run_dir: str, number: int) -> None:
    """Keeps the run's current steps and document as version `number`."""
    folder = os.path.join(run_dir, VERSIONS_DIR, str(number))
    os.makedirs(folder, exist_ok=True)
    for name, path in _version_files(run_dir).items():
        if os.path.isfile(path):
            shutil.copyfile(path, os.path.join(folder, name))


def restore_version(run_dir: str, number: int) -> None:
    """Puts version `number`'s steps and document back."""
    folder = os.path.join(run_dir, VERSIONS_DIR, str(number))
    if not os.path.isfile(os.path.join(folder, "process_model.json")):
        raise FileNotFoundError(f"version {number} isn't kept")
    for name, path in _version_files(run_dir).items():
        source = os.path.join(folder, name)
        if os.path.isfile(source):
            shutil.copyfile(source, path + ".new")
            os.replace(path + ".new", path)


def write(run_dir: str, model: ProcessModel) -> None:
    """Saves the revised steps and writes the document again from them (no model call)."""
    from pipeline import rebuild_document, _load, TEMPLATE_ANALYSIS_FILE
    path = os.path.join(run_dir, "process_model.json")
    with open(path + ".new", "w", encoding="utf-8") as f:
        json.dump(model.model_dump(), f, ensure_ascii=False, indent=2)
    os.replace(path + ".new", path)
    if _load(run_dir, TEMPLATE_ANALYSIS_FILE) is not None:
        rebuild_document(run_dir)
    else:  # the standard layout, without a template
        from render import render_docx
        output = os.path.join(run_dir, "output", "PDD_final.docx")
        render_docx(model, output_path=output + ".new.docx")
        os.replace(output + ".new.docx", output)
    # The project page's figures follow the new steps.
    report_path = os.path.join(run_dir, "report.json")
    report = _load(run_dir, "report.json") or {}
    report.update(steps=len(model.as_is), steps_without_image=[s.number for s in model.as_is if not s.main_frame_path],
                  images_inserted=sum(1 for s in model.as_is if s.main_frame_path),
                  applications=model.applications, decision_points=[s.number for s in model.decision_points])
    with open(report_path + ".new", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    os.replace(report_path + ".new", report_path)


def reassess(run_dir: str, model: ProcessModel, llm, language: str) -> None:
    """The viability assessment again from the corrected steps (one call): it was written from the steps before,
    and a redo often adds what it judged missing (e.g. the rule a decision follows). If it fails, the earlier one
    stays: the steps are what the author asked for."""
    from pipeline import _load, _save, TRANSCRIPT_FILE
    from assess import ASSESSMENT_FILE, assess
    if _load(run_dir, ASSESSMENT_FILE) is None:
        return
    try:
        _save(run_dir, ASSESSMENT_FILE, assess(model, _load(run_dir, TRANSCRIPT_FILE), llm, language=language).model_dump())
    except Exception as exc:  # noqa: BLE001
        log.warning("Assessing %s again after a redo failed: %s", run_dir, exc)


def note_labels(feedback: dict) -> list[str]:
    """The notes as the author sees them, in the order they're numbered for the model."""
    return [("In general" if n is None else f"Step {n}") for n, _ in _notes(feedback)]


def redo(run_dir: str, feedback: dict, version: int, ai: dict | None = None, llm=None) -> dict:
    """Does the run's steps again from the notes and keeps the version before as `version`: the whole redo the
    website starts. Returns {"answers": [{label, outcome, explanation}], "changed": [step numbers]}."""
    import providers
    from pipeline import _load, TEMPLATE_ANALYSIS_FILE
    analysis = _load(run_dir, TEMPLATE_ANALYSIS_FILE)
    language = analysis.get("language") if analysis else "English"
    llm = llm or providers.writing_model(ai)
    model, revision, changed = revise(run_dir, feedback, llm, language=language or "English")
    save_version(run_dir, version)
    reassess(run_dir, model, llm, language or "English")
    write(run_dir, model)
    labels = note_labels(feedback)
    answers = [{"label": labels[a.note - 1], "outcome": a.outcome, "explanation": a.explanation.strip()}
               for a in sorted(revision.answers, key=lambda a: a.note) if 1 <= a.note <= len(labels)]
    return {"answers": answers, "changed": changed}
