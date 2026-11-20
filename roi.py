"""
The return on automating the documented process (ROI). The figures (RoiInputs) come from an ROI template, which
holds a company's assumptions (costs, period, which indicators it reports), and from the project: how often the
process runs and how long one run takes. Where the author gives neither, the AI assessment (assess.py) fills them
in when the recording allows: the time of a run without the narrator's pauses, and how often it runs when the
narration says so; failing that, the time is the recorded one, an upper bound.

They are saved in the run folder as roi.json; the document then gets an ROI section after the template's metrics
table (Pidi's template, whose frequency and average-time answers come from these figures too) or at the end of a
company template.
"""
import json
import logging
import os
import re
from typing import Literal
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

log = logging.getLogger("pdd.roi")
ROI_FILE = "roi.json"
# The sections' texts in the document's language, for languages not written by hand (TEXTS); translated by the
# model once per run, when it's assessed.
TEXTS_FILE = "roi_texts.json"
CURRENCIES = {"BRL": "R$", "USD": "US$", "EUR": "€"}
Indicator = Literal["hours_saved", "savings", "net_savings", "payback", "roi", "fte"]
INDICATORS: list[str] = ["hours_saved", "savings", "net_savings", "payback", "roi", "fte"]
DEFAULT_INDICATORS: list[str] = ["hours_saved", "savings", "net_savings", "payback", "roi"]


class RoiAssumptions(BaseModel):
    """What an ROI template holds: the company's figures, the same for all its processes."""
    hourly_cost: float | None = Field(default=None, ge=0, le=1_000_000)     # of the people doing it, per hour
    build_cost: float | None = Field(default=None, ge=0, le=1_000_000_000)  # building the automation, once
    monthly_cost: float | None = Field(default=None, ge=0, le=1_000_000_000)  # running it: licences, upkeep
    months: int = Field(default=12, ge=1, le=120)                           # the period the ROI is measured over
    hours_per_fte: float = Field(default=168, ge=1, le=744)                 # a full-time person's hours a month
    currency: Literal["BRL", "USD", "EUR"] = "BRL"
    indicators: list[Indicator] = Field(default_factory=lambda: list(DEFAULT_INDICATORS), max_length=len(INDICATORS))


class RoiInputs(RoiAssumptions):
    """A project's figures: its ROI template's (copied in, then editable) and the process's own."""
    template_name: str | None = Field(default=None, max_length=120)          # the ROI template they came from
    runs_per_month: float | None = Field(default=None, ge=0, le=1_000_000)  # None: as the narration says, if it does
    minutes_per_run: float | None = Field(default=None, ge=0, le=100_000)   # None: the AI's estimate, or recorded

    def is_empty(self) -> bool:
        return self.template_name is None and all(v is None for v in (
            self.runs_per_month, self.minutes_per_run, self.hourly_cost, self.build_cost, self.monthly_cost))


class RoiResult(BaseModel):
    """What can be worked out from the figures; None where one they need is missing."""
    runs_per_month: float | None
    runs_source: Literal["given", "narration"] | None
    minutes_per_run: float | None
    minutes_source: Literal["given", "estimated", "recorded"] | None
    hours_saved: float | None            # per month
    savings: float | None                # per month, before the automation's monthly cost
    net_savings: float | None            # per month, after it
    payback_months: float | None         # None also when the automation never pays back (see pays_back)
    pays_back: bool
    roi_percent: float | None            # over `months`
    fte: float | None                    # people's full-time work freed


def calculate(inputs: RoiInputs, recorded_seconds: float | None, assessment=None) -> RoiResult:
    """`assessment`: the run's assess.Assessment, if it has one."""
    runs, runs_source = inputs.runs_per_month, "given"
    if runs is None:
        runs, runs_source = getattr(assessment, "runs_per_month", None), "narration"
    minutes, minutes_source = inputs.minutes_per_run, "given"
    if minutes is None and getattr(assessment, "handling_minutes", None):
        minutes, minutes_source = assessment.handling_minutes, "estimated"
    if minutes is None and recorded_seconds:
        minutes, minutes_source = recorded_seconds / 60, "recorded"
    hours = runs * minutes / 60 if runs is not None and minutes is not None else None
    savings = hours * inputs.hourly_cost if hours is not None and inputs.hourly_cost is not None else None
    net = savings - (inputs.monthly_cost or 0) if savings is not None else None
    build = inputs.build_cost or 0
    pays_back = net is not None and net > 0
    return RoiResult(
        runs_per_month=runs, runs_source=runs_source if runs is not None else None,
        minutes_per_run=minutes, minutes_source=minutes_source if minutes is not None else None,
        hours_saved=hours, savings=savings, net_savings=net,
        payback_months=build / net if pays_back else None, pays_back=pays_back,
        roi_percent=(net * inputs.months - build) / build * 100 if net is not None and build > 0 else None,
        fte=hours / inputs.hours_per_fte if hours is not None else None)


def load_inputs(run_dir: str) -> RoiInputs | None:
    try:
        with open(os.path.join(run_dir, ROI_FILE), encoding="utf-8") as f:
            return RoiInputs.model_validate(json.load(f))
    except FileNotFoundError:
        return None


def save_inputs(run_dir: str, inputs: RoiInputs) -> None:
    """Saves the figures; with none given and no template, removes them (and so the document's ROI section)."""
    path = os.path.join(run_dir, ROI_FILE)
    if inputs.is_empty():
        if os.path.exists(path):
            os.remove(path)
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(inputs.model_dump(), f, indent=2)


# --- In the document ---

# The sections' texts in the template's language: English, Portuguese and Spanish by hand; other languages are
# translated by the model (translate_texts), or get English if that failed. The assessment itself is written by
# the model in the template's language.
TEXTS = {
    "en": {
        "decimal_comma": False,
        "title": "Return on Investment (ROI)", "item": "Item", "value": "Value",
        "intro": "Estimated from the figures given by the author and the time the process takes.",
        "intro_template": "Estimated with the assumptions of the ROI template “{name}” and the time the process takes.",
        "runs": "Runs per month", "time": "Time per run", "recorded": "as recorded, narration included",
        "estimated": "estimated from the recording", "narration": "as said in the recording",
        "hourly": "Cost per hour of the people doing it", "build": "Cost to build the automation (once)",
        "monthly": "Cost to run the automation, per month", "hours_saved": "Hours saved per month",
        "savings": "Savings per month", "net_savings": "Net savings per month (after running costs)",
        "payback": "Payback", "roi": "ROI in {months} months", "fte": "Full-time people freed ({hours} h a month each)",
        "never": "Never: running it costs more than it saves", "immediately": "Immediately (no build cost)",
        "months_unit": "months",
        "times_month": "{n} times a month", "estimate_note": "(estimated)",
        "viability": "Automation Viability", "verdict": "Assessment",
        "verdicts": {"good": "Good candidate for automation", "partial": "Partly automatable",
                     "poor": "Not a good candidate for automation"},
        "strengths": "In favour", "blockers": "Against", "recommendations": "Recommendations",
        "ai_note": "Assessed by AI from the recording; review it before deciding.",
    },
    "pt": {
        "decimal_comma": True,
        "title": "Retorno sobre o Investimento (ROI)", "item": "Item", "value": "Valor",
        "intro": "Estimado a partir dos números informados pelo autor e do tempo que o processo leva.",
        "intro_template": "Estimado com as premissas do modelo de ROI “{name}” e o tempo que o processo leva.",
        "runs": "Execuções por mês", "time": "Tempo por execução", "recorded": "conforme a gravação, com a narração",
        "estimated": "estimado a partir da gravação", "narration": "conforme dito na gravação",
        "hourly": "Custo por hora de quem executa", "build": "Custo para construir a automação (único)",
        "monthly": "Custo mensal para manter a automação", "hours_saved": "Horas economizadas por mês",
        "savings": "Economia por mês", "net_savings": "Economia líquida por mês (após o custo mensal)",
        "payback": "Retorno do investimento (payback)", "roi": "ROI em {months} meses",
        "fte": "Pessoas em tempo integral liberadas ({hours} h por mês cada)",
        "never": "Não se paga: manter a automação custa mais do que ela economiza",
        "immediately": "Imediato (sem custo de construção)", "months_unit": "meses",
        "times_month": "{n} vezes por mês", "estimate_note": "(estimado)",
        "viability": "Viabilidade da Automação", "verdict": "Avaliação",
        "verdicts": {"good": "Boa candidata à automação", "partial": "Automatizável em parte",
                     "poor": "Pouco indicada para automação"},
        "strengths": "A favor", "blockers": "Contra", "recommendations": "Recomendações",
        "ai_note": "Avaliado por IA a partir da gravação; revise antes de decidir.",
    },
    "es": {
        "decimal_comma": True,
        "title": "Retorno de la Inversión (ROI)", "item": "Ítem", "value": "Valor",
        "intro": "Estimado a partir de las cifras informadas por el autor y del tiempo que toma el proceso.",
        "intro_template": "Estimado con los supuestos del modelo de ROI “{name}” y el tiempo que toma el proceso.",
        "runs": "Ejecuciones por mes", "time": "Tiempo por ejecución", "recorded": "según la grabación, con la narración",
        "estimated": "estimado a partir de la grabación", "narration": "según lo dicho en la grabación",
        "hourly": "Costo por hora de quien lo ejecuta", "build": "Costo de construir la automatización (único)",
        "monthly": "Costo mensual de mantener la automatización", "hours_saved": "Horas ahorradas por mes",
        "savings": "Ahorro por mes", "net_savings": "Ahorro neto por mes (tras el costo mensual)",
        "payback": "Recuperación de la inversión (payback)", "roi": "ROI en {months} meses",
        "fte": "Personas a tiempo completo liberadas ({hours} h al mes cada una)",
        "never": "No se recupera: mantener la automatización cuesta más de lo que ahorra",
        "immediately": "Inmediato (sin costo de construcción)", "months_unit": "meses",
        "times_month": "{n} veces por mes", "estimate_note": "(estimado)",
        "viability": "Viabilidad de la Automatización", "verdict": "Evaluación",
        "verdicts": {"good": "Buena candidata a la automatización", "partial": "Automatizable en parte",
                     "poor": "Poco indicada para la automatización"},
        "strengths": "A favor", "blockers": "En contra", "recommendations": "Recomendaciones",
        "ai_note": "Evaluado por IA a partir de la grabación; revíselo antes de decidir.",
    },
}


def _hand_written(language: str) -> str | None:
    name = (language or "").strip().lower()
    code = name.split("-")[0].split("_")[0]  # a code like "pt-BR"; "Estonian" isn't "es"
    if "portug" in name or code == "pt":
        return "pt"
    if "spanish" in name or "español" in name or "espanol" in name or code == "es":
        return "es"
    if "english" in name or code == "en" or not name:
        return "en"
    return None


def texts_for(language: str, translated: dict | None = None) -> dict:
    """The texts for `language`: written by hand, else `translated` (translate_texts), else English."""
    hand = _hand_written(language)
    if hand:
        return TEXTS[hand]
    return translated or TEXTS["en"]


class _Verdicts(BaseModel):
    good: str
    partial: str
    poor: str


class RoiTexts(BaseModel):
    """TEXTS["en"], translated: the same keys, and how the language writes decimals."""
    decimal_comma: bool = Field(description="True if the language writes numbers as 1.234,56; false for 1,234.56")
    verdicts: _Verdicts
    title: str; item: str; value: str; intro: str; intro_template: str; runs: str; time: str  # noqa: E702
    recorded: str; estimated: str; narration: str; hourly: str; build: str; monthly: str  # noqa: E702
    hours_saved: str; savings: str; net_savings: str; payback: str; roi: str; fte: str; never: str  # noqa: E702
    immediately: str; months_unit: str; times_month: str; estimate_note: str; viability: str  # noqa: E702
    verdict: str; strengths: str; blockers: str; recommendations: str; ai_note: str  # noqa: E702


TRANSLATE_PROMPT = """Translate the labels of a business document's ROI and automation-viability sections from \
English into {language}. Keep every key; translate only the values, in the plain, formal style of a business \
document. Keep placeholders in braces exactly as they are ({{name}}, {{months}}, {{hours}}, {{n}}), keep "ROI" and \
units such as "h", and keep the punctuation around values (e.g. “{{name}}”). Also say whether {language} writes \
decimals with a comma."""


def needs_translation(language: str) -> bool:
    return _hand_written(language) is None


def translate_texts(language: str, llm) -> dict:
    """TEXTS["en"] in `language`, by the model. A translated text that lost a placeholder keeps the English one."""
    english = TEXTS["en"]
    result = llm.with_structured_output(RoiTexts).invoke([
        SystemMessage(content=TRANSLATE_PROMPT.format(language=language)),
        HumanMessage(content=json.dumps({k: v for k, v in english.items() if k != "decimal_comma"}, ensure_ascii=False)),
    ])
    texts = result.model_dump()
    for key, value in english.items():
        if isinstance(value, str) and set(re.findall(r"\{\w+\}", value)) - set(re.findall(r"\{\w+\}", texts[key])):
            texts[key] = value
    return texts


def load_texts(run_dir: str) -> dict | None:
    try:
        with open(os.path.join(run_dir, TEXTS_FILE), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def ensure_texts(run_dir: str, language: str, llm) -> dict | None:
    """The run's translated texts, translating them the first time its language needs it. A failure leaves the
    sections in English rather than failing the run."""
    if not needs_translation(language):
        return None
    saved = load_texts(run_dir)
    if saved is not None:
        return saved
    try:
        texts = translate_texts(language, llm)
    except Exception:
        log.exception("translating the ROI texts into %s failed; they stay in English", language)
        return None
    with open(os.path.join(run_dir, TEXTS_FILE), "w", encoding="utf-8") as f:
        json.dump(texts, f, ensure_ascii=False, indent=2)
    return texts


def _number(value: float, decimals: int, comma: bool) -> str:
    text = f"{value:,.{decimals}f}"
    return text.translate(str.maketrans(",.", ".,")) if comma else text


def _formats(t: dict):
    comma = t["decimal_comma"]  # Portuguese and Spanish: 1.234,56

    def number(value: float, decimals: int = 1) -> str:
        return _number(value, decimals, comma)

    def money(value: float, currency: str) -> str:
        sign = "-" if value < 0 else ""
        return f"{sign}{CURRENCIES[currency]} {_number(abs(value), 2, comma)}"

    def minutes(value: float) -> str:  # 30 s, 5 min, 6 min 40 s, 1 h 05 min
        total = round(value * 60)
        if total >= 3600:
            return f"{total // 3600} h {total % 3600 // 60:02d} min"
        if total < 60:
            return f"{total} s"
        return f"{total // 60} min" + (f" {total % 60:02d} s" if total % 60 else "")
    return number, money, minutes


def _count(number, n: float) -> str:
    return number(n, 0 if n == int(n) else 1)


def rows(inputs: RoiInputs, result: RoiResult, language: str, translated: dict | None = None) -> list[tuple[str, str | None]]:
    """The section's table: the figures, then the template's indicators; None where a figure is missing."""
    t = texts_for(language, translated)
    number, money, minutes = _formats(t)
    cur = inputs.currency

    def maybe(value, show):
        return show(value) if value is not None else None
    sources = {"recorded": t["recorded"], "estimated": t["estimated"], "narration": t["narration"]}
    runs = maybe(result.runs_per_month, lambda v: _count(number, v)
                 + (f" ({sources[result.runs_source]})" if result.runs_source in sources else ""))
    time = maybe(result.minutes_per_run, lambda v: minutes(v)
                 + (f" ({sources[result.minutes_source]})" if result.minutes_source in sources else ""))
    if result.net_savings is not None and not result.pays_back:
        payback = t["never"]
    elif result.payback_months == 0:
        payback = t["immediately"]
    else:
        payback = maybe(result.payback_months, lambda m: f"{number(m)} {t['months_unit']}")
    indicators = {
        "hours_saved": (t["hours_saved"], maybe(result.hours_saved, lambda v: f"{number(v)} h")),
        "savings": (t["savings"], maybe(result.savings, lambda v: money(v, cur))),
        "net_savings": (t["net_savings"], maybe(result.net_savings, lambda v: money(v, cur))),
        "payback": (t["payback"], payback),
        "roi": (t["roi"].format(months=inputs.months), maybe(result.roi_percent, lambda v: f"{number(v, 0)}%")),
        "fte": (t["fte"].format(hours=_count(number, inputs.hours_per_fte)), maybe(result.fte, lambda v: number(v, 2))),
    }
    return [
        (t["runs"], runs),
        (t["time"], time),
        (t["hourly"], maybe(inputs.hourly_cost, lambda v: money(v, cur))),
        (t["build"], money(inputs.build_cost or 0, cur)),
        (t["monthly"], money(inputs.monthly_cost or 0, cur)),
    ] + [indicators[key] for key in INDICATORS if key in inputs.indicators]


def metrics_answers(inputs: RoiInputs, result: RoiResult, language: str,
                    translated: dict | None = None) -> dict[int, str]:
    """Answers for the metrics table of Pidi's template, by question (0-based): 3 is the average time of the
    whole activity, 4 how often it is done. The recorded time, an upper bound, isn't given as the average."""
    t = texts_for(language, translated)
    number, _, minutes = _formats(t)
    answers = {}
    if result.minutes_source == "given":
        answers[3] = minutes(result.minutes_per_run)
    elif result.minutes_source == "estimated":
        answers[3] = f"{minutes(result.minutes_per_run)} {t['estimate_note']}"
    if result.runs_per_month is not None:
        answers[4] = t["times_month"].format(n=_count(number, result.runs_per_month))
    return answers
