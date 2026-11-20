"""
Whether the documented process is worth automating, from its process model and narration: a verdict with what
speaks for and against it, and two figures for the ROI (roi.py): how long one run takes without the narrator's
explanations and pauses, and how often the process runs, only when the narration says so (never guessed).
One call per run, saved as assessment.json.
"""
import json
import os
import re
from typing import Literal
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from schemas import ProcessModel

ASSESSMENT_FILE = "assessment.json"
# The narration sent with the model: enough for an hour of talking.
MAX_NARRATION_CHARS = 60_000


class Assessment(BaseModel):
    verdict: Literal["good", "partial", "poor"] = Field(description="good: a good candidate for automation as it "
                                                                    "is; partial: some of it can be automated, or "
                                                                    "only after changes; poor: not a good candidate")
    summary: str = Field(description="Two or three sentences explaining the verdict")
    strengths: list[str] = Field(default_factory=list, max_length=6, description="What makes it suited to "
                                                                                 "automation, specific to this process")
    blockers: list[str] = Field(default_factory=list, max_length=6, description="What makes it hard or risky to "
                                                                                "automate, specific to this process")
    recommendations: list[str] = Field(default_factory=list, max_length=5, description="What to do or check "
                                                                                       "before automating it")
    handling_minutes: float | None = Field(default=None, description="Minutes one run of the process takes when "
                                           "done normally, without the narrator explaining or pausing for the "
                                           "recording; null if it can't be estimated")
    handling_reasoning: str = Field(default="", description="One sentence on how handling_minutes was estimated")
    runs_per_month: float | None = Field(default=None, description="How many times a month the process runs, "
                                         "ONLY if the narration says how often or how much (e.g. 'every day', "
                                         "'about 30 invoices a day'); count 21 working days a month. Null if it "
                                         "isn't said")
    volume_quote: str | None = Field(default=None, description="The narration's exact words runs_per_month comes "
                                                               "from, or null")


PROMPT = """You assess whether a business process is worth automating with RPA, for the team that will decide. You are given the process documented from a screen recording: its steps (with the application, any decision, and how long each took in the recording), inputs, business rules and exceptions, and the narrator's words.

Judge it by the usual RPA criteria, and only from what is given:
- rule-based steps vs. steps needing human judgment (reading free text, deciding case by case);
- structured, digital inputs (spreadsheets, systems, forms) vs. unstructured ones (scanned documents, free-form emails);
- stable applications vs. ones hard to automate (remote desktops such as Citrix, frequently changing screens, CAPTCHAs);
- how many decisions and exceptions there are, and whether each has a clear rule;
- volume and repetitiveness, when they are said.

Be specific to this process: name the steps, applications and inputs. Don't invent volumes, frequencies or costs that aren't said. The recorded times include the narrator's explanations and pauses: estimate the time of one normal run from the steps themselves.

Write the summary, strengths, blockers, recommendations and reasoning in {language}."""


def _process_text(model: ProcessModel) -> str:
    lines = [f"Process: {model.process_name}", f"Summary: {model.project_proposal}", "", "Steps:"]
    for step in model.as_is:
        took = f", {step.duration_seconds:.0f} s in the recording" if step.duration_seconds is not None else ""
        lines.append(f"{step.number}. [{step.application or 'unknown application'}{took}] {step.instruction}")
        if step.decision:
            lines.append(f"   Decision: {step.decision}")
    if model.recorded_seconds:
        lines.append(f"\nWhole recording of the process: {model.recorded_seconds / 60:.1f} minutes")
    if model.inputs:
        lines += ["", "Inputs:"] + [f"- {i.name} ({i.kind}{', from ' + i.source if i.source else ''})" for i in model.inputs]
    if model.requirements:
        lines += ["", "Business rules and requirements:"] + [f"- {r.description}" for r in model.requirements]
    exceptions = model.business_exceptions + model.system_exceptions
    if exceptions:
        lines += ["", "Exceptions:"] + [f"- {e.name}: {e.action} -> {e.action_to_be_taken}" for e in exceptions]
    return "\n".join(lines)


def _squash(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def assess(model: ProcessModel, transcript: list[dict] | None, llm, language: str = "English") -> Assessment:
    narration = " ".join(seg["text"] for seg in transcript or [])
    message = _process_text(model) + "\n\nNarration:\n" + (narration[:MAX_NARRATION_CHARS] or "(none: the recording has no narration)")
    result = llm.with_structured_output(Assessment).invoke([
        SystemMessage(content=PROMPT.format(language=language)),
        HumanMessage(content=message),
    ])
    return check(result, model, narration)


def check(result: Assessment, model: ProcessModel, narration: str) -> Assessment:
    """Keeps the figures honest: the time of a run can't exceed the recording's, and a frequency counts only
    with the narration's own words behind it."""
    recorded = model.recorded_seconds
    if result.handling_minutes is not None:
        if result.handling_minutes <= 0:
            result.handling_minutes = None
        elif recorded:
            result.handling_minutes = min(result.handling_minutes, recorded / 60)
    quote = _squash(result.volume_quote or "")
    if result.runs_per_month is not None and (not quote or quote not in _squash(narration)
                                              or not 0 < result.runs_per_month <= 1_000_000):
        result.runs_per_month = result.volume_quote = None
    if result.runs_per_month is None:
        result.volume_quote = None
    return result


def load(run_dir: str) -> Assessment | None:
    try:
        with open(os.path.join(run_dir, ASSESSMENT_FILE), encoding="utf-8") as f:
            return Assessment.model_validate(json.load(f))
    except FileNotFoundError:
        return None
