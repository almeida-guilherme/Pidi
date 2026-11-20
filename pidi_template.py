"""
Pidi's own template, the one used when no company template is given, in any language.

English and Brazilian Portuguese are translated by hand and ship in templates/. Any other language is
translated by the model from the English one, the first time it is asked for: every piece of the template's
fixed text (headings, table labels, introduction, footer) is replaced in place, so the formatting and the
blocks stay the same and the hand-checked analysis still applies; only its language-specific values
(placeholders, flowchart labels, the "to be completed" marker, the step prefix) are translated with it. The
result is kept in a cache folder, one per language, so each language is translated once.

This module is light (no pipeline imports), so the website can use the language list without loading the
pipeline.
"""
import hashlib
import json
import os
import re
import threading
from docx import Document
from docx.oxml.ns import qn
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

_TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
# Translated by hand: the template and its analysis, with the same blocks.
PIDI_TEMPLATES = {
    "en": (os.path.join(_TEMPLATES, "pidi.en.docx"), os.path.join(_TEMPLATES, "pidi.en.analysis.json")),
    "pt-BR": (os.path.join(_TEMPLATES, "pidi.docx"), os.path.join(_TEMPLATES, "pidi.analysis.json")),
}
DEFAULT_CACHE = os.path.join(_TEMPLATES, "translated")
# Offered in lists, each with its own name for itself and its English name (for the model); the hand-made
# ones first. Any other language can be asked for by name.
LANGUAGES = {
    "en": ("English", "English"),
    "pt-BR": ("Português (Brasil)", "Brazilian Portuguese"),
    "es": ("Español", "Spanish"),
    "fr": ("Français", "French"),
    "de": ("Deutsch", "German"),
    "it": ("Italiano", "Italian"),
    "nl": ("Nederlands", "Dutch"),
    "pl": ("Polski", "Polish"),
    "pt-PT": ("Português (Portugal)", "European Portuguese"),
    "ja": ("日本語", "Japanese"),
    "zh-CN": ("中文（简体）", "Simplified Chinese"),
}
# A language typed by name: letters (any script), spaces and a few separators, e.g. "Tagalog", "Swiss German".
_NAME = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ ()'.,-]){1,39}$")
_STEP = re.compile(r"^(<?)S(\d+)(>?\s*)$")  # the example step labels of the English template: "S1  ", "<S13>"
_LETTER = re.compile(r"[^\W\d_]")
_lock = threading.Lock()


def normalize_language(value: str) -> str:
    """A code of LANGUAGES, or a language's name as typed (tidied); ValueError if it isn't one."""
    value = " ".join(value.split())
    if value in LANGUAGES:
        return value
    for code, names in LANGUAGES.items():
        if value.casefold() in (n.casefold() for n in names):
            return code
    if not _NAME.match(value):
        raise ValueError("Choose a language from the list, or type its name (letters only, up to 40).")
    return value


def language_name(language: str) -> str:
    """The language's English name, as the model is told it."""
    return LANGUAGES[language][1] if language in LANGUAGES else language


def is_translated(language: str | None) -> bool:
    """Whether Pidi's template in this language is translated by the model (not shipped)."""
    return bool(language) and language not in PIDI_TEMPLATES


class TranslatedText(BaseModel):
    id: int
    text: str


class TemplateTranslation(BaseModel):
    texts: list[TranslatedText] = Field(description="Every text given, by its id, translated")
    decision: str = Field(description="'Decision' (a decision diamond in a flowchart), translated")
    start: str = Field(description="'Start' (the flowchart's start), translated")
    end: str = Field(description="'End' (the flowchart's end), translated")
    missing_label: str = Field(description="'[To be completed]' (a marker for information the author must "
                                           "still fill in), translated, keeping the brackets")
    step_prefix: str = Field(description="One or two letters that stand for 'Step' in step labels such as 'S1', "
                                         "'S2' (e.g. 'P' for Spanish 'Paso', 'É' for French 'Étape'); in "
                                         "languages without such abbreviations, a short word")


TRANSLATE_PROMPT = """You translate the fixed text of a Process Design Document (PDD) template from English into {language}.

Each text is one piece of the document (a heading, a table label, a sentence, an example value), with an id. Return every one, by its id, translated.
- Keep the template's tone: formal and impersonal, as in a corporate process document.
- Keep numbering ("4.2. "), punctuation, leading and trailing spaces, and line breaks exactly as they are.
- Keep the angle brackets of placeholders such as "<Process name>": translate only the words inside them.
- Keep markers like ⟦0⟧ exactly as they are, where they are.
- Keep names of products, companies, systems and technical terms that are not translated in {language} (SAP, Outlook, Single Sign On, PDD, AS IS, pidi).
- A date placeholder such as "<dd/mm/yyyy>" becomes the date pattern people write in {language}, with the letters they use."""


def pidi_template(language: str, llm=None, cache_dir: str | None = None,
                  progress=None) -> tuple[str, str]:
    """The template and analysis files of Pidi's template in `language` (a code of LANGUAGES or a language's
    name). Shipped ones are returned as they are; others are translated with `llm` the first time and cached."""
    if language in PIDI_TEMPLATES:
        return PIDI_TEMPLATES[language]
    source, source_analysis = PIDI_TEMPLATES["en"]
    folder = os.path.join(cache_dir or DEFAULT_CACHE, _cache_name(language, source, source_analysis))
    template, analysis = os.path.join(folder, "template.docx"), os.path.join(folder, "analysis.json")
    with _lock:  # two runs asking for a new language at once translate it once
        if os.path.isfile(template) and os.path.isfile(analysis):
            if progress:
                progress("translate_template", f"Pidi's template in {language_name(language)} already translated", True)
            return template, analysis
        if llm is None:
            raise ValueError(f"Pidi's template in {language_name(language)} needs a model to translate it")
        if progress:
            progress("translate_template", f"Translating Pidi's template into {language_name(language)}", False)
        os.makedirs(folder, exist_ok=True)
        translate(source, source_analysis, language_name(language), llm, template, analysis)
    if progress:
        progress("translate_template", f"Pidi's template translated into {language_name(language)}", True)
    return template, analysis


def is_cached(language: str, cache_dir: str | None = None) -> bool:
    """Whether Pidi's template in `language` is ready without translating it (shipped, or translated before)."""
    if language in PIDI_TEMPLATES:
        return True
    folder = os.path.join(cache_dir or DEFAULT_CACHE, _cache_name(language, *PIDI_TEMPLATES["en"]))
    return os.path.isfile(os.path.join(folder, "template.docx")) and os.path.isfile(os.path.join(folder, "analysis.json"))


def _cache_name(language: str, *sources: str) -> str:
    """Folder per language and per version of the English template, so editing it translates again."""
    digest = hashlib.sha256(language.casefold().encode())
    for path in sources:
        with open(path, "rb") as f:
            digest.update(f.read())
    slug = re.sub(r"[^a-z0-9]+", "-", language.casefold()).strip("-")
    return f"{slug or 'language'}-{digest.hexdigest()[:12]}"


def _runs(doc):
    """Every run of text: the body, page headers and footers."""
    parts = [doc.element.body] + [h._element for s in doc.sections
                                  for h in (s.header, s.footer, s.first_page_header, s.first_page_footer)]
    for part in parts:
        for run in part.iter(qn("w:r")):
            texts = list(run.iter(qn("w:t")))
            if texts:
                yield texts


def translate(source: str, source_analysis: str, language: str, llm, target: str, target_analysis: str) -> None:
    """Writes `source` (the English template) translated into `language` to `target`, and its analysis."""
    with open(source_analysis, encoding="utf-8") as f:
        analysis = json.load(f)
    doc = Document(source)
    placeholders = [p["text"] for p in analysis["placeholders"]]
    step = analysis["step_format"]
    # Example values of the step details line are found by the renderer as written; they stay.
    keep = {step.get("details_application"), step.get("details_time"), "pidi"}

    # The texts to translate, each once. Placeholders inside a longer text become markers, so they are
    # translated once and stay identical wherever they appear (the fill finds them by their text).
    texts: list[str] = list(placeholders)
    for run in _runs(doc):
        text = "".join(t.text or "" for t in run)
        marked = _mark(text, placeholders)
        if text in keep or _STEP.match(text) or not _LETTER.search(text) or marked in texts:
            continue
        texts.append(marked)
    result = llm.with_structured_output(TemplateTranslation).invoke([
        SystemMessage(content=TRANSLATE_PROMPT.format(language=language)),
        HumanMessage(content=json.dumps([{"id": i, "text": t} for i, t in enumerate(texts)], ensure_ascii=False)),
    ])
    translated = {item.id: item.text for item in result.texts if 0 <= item.id < len(texts) and item.text.strip()}
    # A text the model left out stays in English rather than failing the run.
    by_text = {t: translated.get(i, t) for i, t in enumerate(texts)}
    names = {p: by_text[p] for p in placeholders}
    prefix = result.step_prefix.strip() or "S"

    for run in _runs(doc):
        text = "".join(t.text or "" for t in run)
        step_label = _STEP.match(text)
        if step_label:
            new = f"{step_label.group(1)}{prefix}{step_label.group(2)}{step_label.group(3)}"
        elif text in by_text:
            new = by_text[text]
        elif _mark(text, placeholders) in by_text:
            new = _unmark(by_text[_mark(text, placeholders)], placeholders, names)
        else:
            continue
        run[0].text = new
        run[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        for t in run[1:]:
            t.text = ""
    doc.core_properties.language = language
    doc.save(target)

    analysis["language"] = language
    analysis["missing_label"] = result.missing_label.strip() or analysis["missing_label"]
    analysis["labels"] = {"decision": result.decision, "start": result.start, "end": result.end}
    step["label"] = prefix + "{n}"
    for p in analysis["placeholders"]:
        old, p["text"] = p["text"], names[p["text"]]
        p["meaning"] = p["meaning"].replace(old.strip("<>"), p["text"].strip("<>"))
    with open(target_analysis, "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)


def _mark(text: str, placeholders: list[str]) -> str:
    for i, p in enumerate(placeholders):
        if p != text:
            text = text.replace(p, f"⟦{i}⟧")
    return text


def _unmark(text: str, placeholders: list[str], names: dict[str, str]) -> str:
    for i, p in enumerate(placeholders):
        text = text.replace(f"⟦{i}⟧", names[p])
    return text
