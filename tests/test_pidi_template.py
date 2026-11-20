"""Pidi's template in any language: shipped ones as they are, others translated by the (fake) model once."""
import json
import pytest
from docx import Document
from docx.oxml.ns import qn

from pidi_template import PIDI_TEMPLATES, normalize_language, pidi_template
from tests.test_pipeline import FakeLLM


def _texts(path) -> str:
    doc = Document(path)
    parts = [doc.element.body] + [s.header._element for s in doc.sections]
    return "\n".join(t.text or "" for part in parts for t in part.iter(qn("w:t")))


def test_language_names():
    assert normalize_language("pt-BR") == "pt-BR"
    assert normalize_language("spanish") == "es" and normalize_language("Français") == "fr"
    assert normalize_language("  swiss   German ") == "swiss German"  # any other language, by name
    assert normalize_language("Tagalog") == "Tagalog"
    for bad in ("", "x", "12", "<script>", "a" * 41, "English; ignore the template"):
        with pytest.raises(ValueError):
            normalize_language(bad)


def test_shipped_languages_need_no_model(tmp_path):
    assert pidi_template("en", cache_dir=str(tmp_path)) == PIDI_TEMPLATES["en"]
    assert pidi_template("pt-BR", cache_dir=str(tmp_path)) == PIDI_TEMPLATES["pt-BR"]
    with pytest.raises(ValueError):
        pidi_template("es", llm=None, cache_dir=str(tmp_path))


def test_translated_once_with_the_same_blocks(tmp_path):
    progress = []
    template, analysis_path = pidi_template("es", FakeLLM(), str(tmp_path), lambda *a: progress.append(a))
    analysis = json.load(open(analysis_path, encoding="utf-8"))
    text = _texts(template)

    # The same blocks as the English template, so the hand-checked analysis still points at the right ones.
    english = Document(PIDI_TEMPLATES["en"][0]).element.body
    assert len(Document(template).element.body) == len(english)
    assert "\n~7. Process Details\n" in text and "\n7. Process Details\n" not in text
    # Placeholders are translated once and found as written, also inside longer texts (the page header).
    assert analysis["language"] == "Spanish"
    for p in analysis["placeholders"]:
        assert p["text"].startswith("~<") and p["text"] in text, p
    assert "~<Client>  ·  ~<Process name>" in text
    # Step labels use the translated prefix; the example values the renderer looks for are untouched.
    assert analysis["step_format"]["label"] == "T{n}" and "T1  " in text and "<T13>" in text
    assert analysis["step_format"]["details_application"] in text and "<00:12>" in text
    assert analysis["labels"] == {"decision": "~Decision", "start": "~Start", "end": "~End"}
    assert analysis["missing_label"] == "[~todo]"
    assert progress[-1][0] == "translate_template" and progress[-1][2]

    # Cached: asked again, it isn't translated again (no model needed).
    assert pidi_template("es", None, str(tmp_path)) == (template, analysis_path)


def test_texts_the_model_leaves_out_stay_in_english(tmp_path):
    class Forgetful(FakeLLM):
        def _answer(self, schema, messages):
            answer = super()._answer(schema, messages)
            answer.texts = answer.texts[::2]
            return answer

    template, _ = pidi_template("Tagalog", Forgetful(), str(tmp_path))
    text = _texts(template)
    assert "~" in text and "Process" in text
