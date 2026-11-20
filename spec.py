"""
The automation spec: a run's process written as Markdown for an agent that builds the automation (a separate
project), rather than for people like the PDD. It is written from the run's saved results (process model,
captions, assessment, ROI figures), with no model calls.

The spec describes the process, never a technology: which screens, fields, visible text, decisions and
exceptions, not Playwright or UiPath, so the same file can feed any target. Its format has a version
(SPEC_VERSION, in the front matter); a change that breaks readers increases it.

    automation.md            front matter (JSON values, valid YAML) and the sections below
    screenshots/S<n>.jpg     the screenshot of each step that has one

The text of steps, rules and exceptions is in the run's document language; headings and keys are in English.
"""
import io
import json
import os
import zipfile
from datetime import datetime, timezone

SPEC_VERSION = 1
# Visible text listed per step: the step's screenshot first, then its other frames.
MAX_VISIBLE_TEXT = 25


def _load(run_dir: str, name: str):
    try:
        with open(os.path.join(run_dir, name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def _frame_id(path: str | None) -> str | None:
    return os.path.splitext(os.path.basename(path))[0] if path else None


def _visible_text(step: dict, captions: dict) -> list[str]:
    frames = [_frame_id(step.get("main_frame_path"))] + list(step.get("frame_ids") or [])
    seen, texts = set(), []
    for frame in frames:
        for text in (captions.get(frame) or {}).get("visible_text", []):
            key = text.strip().lower()
            if key and key not in seen:
                seen.add(key)
                texts.append(text.strip())
    return texts[:MAX_VISIBLE_TEXT]


def _yaml(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _line(text: str) -> str:
    """One line of Markdown: no line breaks, and no pipes that would split a table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def build(run_dir: str, name: str | None = None) -> tuple[str, dict[str, str]]:
    """The spec's Markdown, and the screenshots it refers to ({path in the spec: file on disk})."""
    model = _load(run_dir, "process_model.json")
    if model is None:
        raise FileNotFoundError(f"{run_dir} has no process model; the run hasn't finished")
    captions = {c["id"]: c for c in _load(run_dir, "captions.json") or []}
    assessment = _load(run_dir, "assessment.json")
    roi = _load(run_dir, "roi.json") or {}
    steps = model.get("as_is", [])
    applications = list(dict.fromkeys(s["application"] for s in steps if s.get("application")))

    runs = roi.get("runs_per_month")
    runs_source = "given" if runs is not None else None
    if runs is None and assessment and assessment.get("runs_per_month"):
        runs, runs_source = assessment["runs_per_month"], "narration"
    minutes = roi.get("minutes_per_run")
    minutes_source = "given" if minutes is not None else None
    if minutes is None and assessment and assessment.get("handling_minutes"):
        minutes, minutes_source = assessment["handling_minutes"], "estimated"

    front = {
        "spec_version": SPEC_VERSION,
        "process": name or model.get("process_name"),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": "recording",
        "applications": applications,
        "inputs": [{"name": i["name"], "kind": i["kind"], "source": i.get("source") or None}
                   for i in model.get("inputs", [])],
        "steps": len(steps),
        "decisions": [s["number"] for s in steps if s.get("decision")],
        "runs_per_month": runs, "runs_per_month_source": runs_source,
        "minutes_per_run": round(minutes, 2) if minutes is not None else None, "minutes_per_run_source": minutes_source,
        "viability": assessment.get("verdict") if assessment else None,
    }
    out = ["---"] + [f"{key}: {_yaml(value)}" for key, value in front.items()] + ["---", ""]
    out += [f"# {front['process']}", "", model.get("project_proposal", "").strip(), ""]

    if applications:
        out += ["## Applications", ""] + [f"- {_line(a)}" for a in applications] + [""]
    if model.get("inputs"):
        out += ["## Inputs", "", "| Name | Kind | Source |", "|---|---|---|"]
        out += [f"| {_line(i['name'])} | {i['kind']} | {_line(i.get('source') or '?')} |" for i in model["inputs"]]
        out.append("")
    if model.get("key_names"):
        out += ["## Names to match exactly", "",
                "Files, folders, sites, screens and buttons as they are really spelled; other spellings seen in the "
                "recording are misreadings.", ""]
        for k in model["key_names"]:
            variants = f" (not: {', '.join(_line(v) for v in k['variants'])})" if k.get("variants") else ""
            out.append(f"- `{_line(k['name'])}`{variants}")
        out.append("")

    screenshots: dict[str, str] = {}
    out += ["## Steps", "",
            "In order, as done in the recording. Times are where each step starts in the recording.", ""]
    for step in steps:
        head = [f"S{step['number']}"]
        if step.get("application"):
            head.append(step["application"])
        if step.get("time"):
            head.append(step["time"])
        out += [f"### {' · '.join(_line(h) for h in head)}", "", step["instruction"].strip(), ""]
        if step.get("decision"):
            out += [f"- **Decision:** {_line(step['decision'])}"]
        texts = _visible_text(step, captions)
        if texts:
            out += [f"- **Visible on screen:** {', '.join(_yaml(t) for t in texts)}"]
        frame = step.get("main_frame_path")
        if frame and not os.path.isfile(frame):  # saved from another working folder: the run's own copy
            frame = os.path.join(run_dir, "frames", os.path.basename(frame))
        if frame and os.path.isfile(frame):
            path = f"screenshots/S{step['number']}.jpg"
            screenshots[path] = frame
            out += [f"- **Screenshot:** [{path}]({path})"]
        if step.get("duration_seconds") is not None:
            out += [f"- **Recorded duration:** {step['duration_seconds']:.0f} s (narration included)"]
        out.append("")

    if model.get("requirements"):
        out += ["## Business rules and requirements", "", "| Kind | Rule |", "|---|---|"]
        out += [f"| {r['category']} | {_line(r['description'])} |" for r in model["requirements"]]
        out.append("")
    for kind, key in (("Business", "business_exceptions"), ("System", "system_exceptions")):
        if model.get(key):
            out += [f"## {kind} exceptions", "", "| Exception | When | Parameters | What to do |", "|---|---|---|---|"]
            out += [f"| {_line(e['name'])} | {_line(e['action'])} | {_line(e.get('parameters') or '')} | "
                    f"{_line(e['action_to_be_taken']) or '?'} |" for e in model[key]]
            out.append("")

    if assessment:
        out += ["## Viability", "", f"**{assessment['verdict']}**: {assessment['summary'].strip()}", ""]
        for title, key in (("In favour", "strengths"), ("Against", "blockers"), ("Recommendations", "recommendations")):
            if assessment.get(key):
                out += [f"**{title}**", ""] + [f"- {_line(item)}" for item in assessment[key]] + [""]

    questions = _open_questions(model, steps, screenshots, runs, assessment)
    out += ["## Open questions", "",
            "To answer before building the automation; they are not in the recording." if questions
            else "None found in the recording.", ""]
    out += [f"- {q}" for q in questions]
    return "\n".join(out).rstrip() + "\n", screenshots


def _open_questions(model, steps, screenshots, runs, assessment) -> list[str]:
    questions = []
    if runs is None:
        questions.append("How often does the process run (runs per month), and how many items per run?")
    for i in model.get("inputs", []):
        if not i.get("source"):
            questions.append(f"Where does the input “{_line(i['name'])}” come from?")
    for kind in ("business_exceptions", "system_exceptions"):
        for e in model.get(kind, []):
            if not (e.get("action_to_be_taken") or "").strip():
                questions.append(f"What should be done when “{_line(e['name'])}” happens?")
    without = [s["number"] for s in steps if f"screenshots/S{s['number']}.jpg" not in screenshots]
    if without:
        questions.append(f"Steps without a screenshot ({', '.join(f'S{n}' for n in without)}): "
                         "which screen are they done on?")
    if not any(r["category"] == "access" for r in model.get("requirements", [])) and model.get("as_is"):
        questions.append("Which accounts and permissions does the automation need in each application?")
    if assessment and assessment.get("verdict") != "good":
        questions.append("The viability assessment has doubts (see Viability): confirm the process should be automated.")
    return questions


def write_zip(run_dir: str, name: str | None = None) -> bytes:
    """automation.md and its screenshots, as a zip."""
    markdown, screenshots = build(run_dir, name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("automation.md", markdown)
        for path, source in screenshots.items():
            z.write(source, path)
    return buffer.getvalue()


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit("Usage: python spec.py <run folder>   (writes <run folder>/output/automation.zip)")
    target = os.path.join(sys.argv[1], "output", "automation.zip")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as f:
        f.write(write_zip(sys.argv[1]))
    print(f"Written {target}")
