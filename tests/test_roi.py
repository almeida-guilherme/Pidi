"""The ROI of automating a project's process and whether it's worth it: ROI templates hold a company's assumptions,
projects get a copy, the AI assessment fills in what the recording shows, and the document gets both sections
(rebuilt without the model when the figures change)."""
import io
import pytest
from docx import Document

from assess import Assessment, check
from roi import RoiInputs, calculate
from schemas import ProcessModel, Step
from tests.test_web import make_client, register, upload, video, wait_for  # noqa: F401

ASSUMPTIONS = {"hourly_cost": 45, "build_cost": 8000, "monthly_cost": 300, "months": 12, "hours_per_fte": 160,
               "currency": "BRL", "indicators": ["hours_saved", "savings", "net_savings", "payback", "roi", "fte"]}
FIGURES = {**ASSUMPTIONS, "runs_per_month": 120}


def test_calculation():
    result = calculate(RoiInputs(**FIGURES), recorded_seconds=400)
    assert result.minutes_source == "recorded" and result.minutes_per_run == pytest.approx(400 / 60)
    assert result.runs_source == "given" and result.hours_saved == pytest.approx(13.333, abs=0.001)
    assert result.savings == pytest.approx(600) and result.net_savings == pytest.approx(300)
    assert result.payback_months == pytest.approx(26.667, abs=0.001) and result.roi_percent == pytest.approx(-55)
    assert result.fte == pytest.approx(13.333 / 160, abs=0.001)

    # The AI's estimate beats the recorded time, the author's figures beat both.
    ai = Assessment(verdict="good", summary="", handling_minutes=4, runs_per_month=200, volume_quote="x")
    estimated = calculate(RoiInputs(**ASSUMPTIONS), recorded_seconds=400, assessment=ai)
    assert (estimated.minutes_source, estimated.minutes_per_run) == ("estimated", 4)
    assert (estimated.runs_source, estimated.runs_per_month) == ("narration", 200)
    given = calculate(RoiInputs(**FIGURES, minutes_per_run=10), recorded_seconds=400, assessment=ai)
    assert (given.minutes_source, given.runs_source, given.savings) == ("given", "given", pytest.approx(900))

    # Running it costs more than it saves: it never pays back. Figures missing: only what they allow.
    costly = calculate(RoiInputs(**{**FIGURES, "monthly_cost": 700}), recorded_seconds=400)
    assert costly.net_savings == pytest.approx(-100) and not costly.pays_back and costly.payback_months is None
    # No costs given: it pays back at once, and there's no ROI to give without a build cost.
    free = calculate(RoiInputs(runs_per_month=120, hourly_cost=45), recorded_seconds=400)
    assert free.payback_months == 0 and free.pays_back and free.roi_percent is None
    from roi import rows
    assert ("Payback", "Immediately (no build cost)") in rows(RoiInputs(runs_per_month=120, hourly_cost=45), free, "en")
    partial = calculate(RoiInputs(runs_per_month=120), recorded_seconds=None)
    assert partial.hours_saved is None and partial.savings is None and partial.roi_percent is None
    assert RoiInputs().is_empty() and not RoiInputs(template_name="Finance").is_empty()


def test_assessment_figures_are_kept_honest():
    model = ProcessModel(process_name="p", project_proposal="", as_is=[
        Step(number=1, time="00:00", instruction="a", short_label="a", frame_ids=[], start_seconds=0, duration_seconds=60),
        Step(number=2, time="01:00", instruction="b", short_label="b", frame_ids=[], start_seconds=60, duration_seconds=60)])
    narration = "We do this about thirty times a day, every working day."

    def checked(**fields):
        return check(Assessment(verdict="good", summary="", **fields), model, narration)
    assert checked(handling_minutes=9).handling_minutes == 2  # never longer than the recording
    assert checked(handling_minutes=0).handling_minutes is None
    kept = checked(runs_per_month=630, volume_quote="about thirty times a day")
    assert kept.runs_per_month == 630 and kept.volume_quote == "about thirty times a day"
    assert checked(runs_per_month=630).runs_per_month is None  # no words behind it
    assert checked(runs_per_month=630, volume_quote="fifty times a week").runs_per_month is None  # not said


def document_text(client, run_id) -> str:
    doc = Document(io.BytesIO(client.get(f"/api/runs/{run_id}/document").content))
    lines = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        lines += [" | ".join(cell.text for cell in row.cells) for row in table.rows]
    return "\n".join(lines)


def test_every_project_is_assessed(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        run = wait_for(client, upload(client, video).json()["id"])
        assert run["stages"][5] == "assess"
        roi = client.get(f"/api/runs/{run['id']}").json()["roi"]
        assert roi["assessment"]["verdict"] == "good" and roi["assessment"]["blockers"] == ["Waits for an approval"]
        assert roi["result"]["minutes_source"] == "estimated" and roi["result"]["savings"] is None
        text = document_text(client, run["id"])
        # Without ROI figures: the viability section alone, right after the metrics table, which gets the time.
        assert "6.4. Automation Viability" in text and "Return on Investment" not in text
        assert "Assessment: Good candidate for automation" in text and "•  Waits for an approval" in text
        assert "average time to complete the whole activity? | 30 s (estimated)" in text


def test_roi_templates_and_projects(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        created = client.post("/api/roi-templates", json={"name": "Finance", "assumptions": ASSUMPTIONS})
        assert created.status_code == 201, created.text
        template = created.json()
        assert client.post("/api/roi-templates", json={"name": "", "assumptions": {}}).status_code == 422
        assert client.post("/api/roi-templates", json={"name": "x", "assumptions": {"hourly_cost": -1}}).status_code == 422
        assert client.post("/api/roi-templates", json={"name": "x", "assumptions": {"indicators": ["magic"]}}).status_code == 422
        assert [t["name"] for t in client.get("/api/roi-templates").json()] == ["Finance"]

        with open(video, "rb") as f:
            run = client.post("/api/runs", files={"file": ("x.mp4", f, "video/mp4")},
                              data={"consent": "true", "roi_template_id": template["id"]}).json()
        run = wait_for(client, run["id"])
        roi = run["roi"]
        assert roi["inputs"]["template_name"] == "Finance" and roi["inputs"]["hourly_cost"] == 45
        assert roi["result"]["runs_per_month"] is None  # the narration doesn't say; the author gives it

        # Changing the template later doesn't change the project's copy.
        changed = client.put(f"/api/roi-templates/{template['id']}",
                             json={"name": "Finance 2", "assumptions": {**ASSUMPTIONS, "hourly_cost": 99}})
        assert changed.status_code == 200 and changed.json()["assumptions"]["hourly_cost"] == 99
        assert client.get(f"/api/runs/{run['id']}").json()["roi"]["inputs"]["hourly_cost"] == 45

        saved = client.put(f"/api/runs/{run['id']}/roi", json={**roi["inputs"], "runs_per_month": 120,
                                                               "minutes_per_run": 5})
        assert saved.status_code == 200, saved.text
        assert saved.json()["roi"]["result"]["savings"] == pytest.approx(450)
        text = document_text(client, run["id"])
        assert "6.4. Return on Investment (ROI)" in text and "6.5. Automation Viability" in text
        assert "ROI template “Finance”" in text
        assert "Savings per month | R$ 450.00" in text and "ROI in 12 months | -78%" in text
        assert "Full-time people freed (160 h a month each) | 0.06" in text
        assert "Time per run | 5 min" in text
        assert "How often is it performed?\nE.g. daily, weekly, monthly | 120 times a month" in text

        # Indicators left out of the template aren't in the document.
        client.put(f"/api/runs/{run['id']}/roi", json={**saved.json()["roi"]["inputs"], "indicators": ["roi"]})
        text = document_text(client, run["id"])
        assert "ROI in 12 months" in text and "Savings per month" not in text

        # Deleting the template keeps the projects' copies; templates and projects are private.
        assert client.delete(f"/api/roi-templates/{template['id']}").status_code == 204
        assert client.get(f"/api/runs/{run['id']}").json()["roi"]["inputs"]["template_name"] == "Finance"
        export = client.get("/api/account/export").json()
        assert export["projects"][0]["roi"]["template_name"] == "Finance" and export["roi_templates"] == []
        mine = client.post("/api/roi-templates", json={"name": "Mine", "assumptions": ASSUMPTIONS}).json()
        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.get(f"/api/roi-templates/{mine['id']}").status_code == 404
        assert client.put(f"/api/runs/{run['id']}/roi", json=FIGURES).status_code == 404
        with open(video, "rb") as f:
            refused = client.post("/api/runs", files={"file": ("x.mp4", f, "video/mp4")},
                                  data={"consent": "true", "roi_template_id": mine["id"]})
        assert refused.status_code == 404


def test_roi_in_portuguese_and_before_the_document_is_ready(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        client.app.state.jobs.stop()  # it stays queued while the figures are given
        with open(video, "rb") as f:
            run = client.post("/api/runs", files={"file": ("x.mp4", f, "video/mp4")},
                              data={"consent": "true", "language": "pt-BR"}).json()
        saved = client.put(f"/api/runs/{run['id']}/roi", json={**FIGURES, "monthly_cost": 700})
        assert saved.status_code == 200 and saved.json()["status"] == "queued"
    with make_client(tmp_path) as client:  # the server again: the queued project runs
        register(client)
        assert wait_for(client, run["id"])["status"] == "done"
        text = document_text(client, run["id"])
        assert "Retorno sobre o Investimento (ROI)" in text and "Viabilidade da Automação" in text
        assert "Custo para construir a automação (único) | R$ 8.000,00" in text
        assert "Não se paga" in text  # running it costs more than it saves
        # No figures at all: the ROI section goes; the viability stays.
        assert client.put(f"/api/runs/{run['id']}/roi", json={}).status_code == 200
        text = document_text(client, run["id"])
        assert "Retorno sobre o Investimento" not in text and "Viabilidade da Automação" in text


def test_roi_at_the_end_of_a_company_template(tmp_path, video):
    from tests.test_web import fake_template_run
    from tests.template_fixture import build_template
    template = build_template(str(tmp_path / "Company PDD.docx"), str(tmp_path / "example.png"))
    with make_client(tmp_path, run_fn=fake_template_run) as client:
        register(client)
        with open(template, "rb") as f:
            template_id = client.post("/api/templates", files={"file": ("Company PDD.docx", f)}).json()["id"]
        with open(video, "rb") as f:
            run = client.post("/api/runs", files={"file": ("Invoices.mp4", f, "video/mp4")},
                              data={"consent": "true", "template_id": template_id}).json()
        assert wait_for(client, run["id"])["status"] == "done"
        assert client.put(f"/api/runs/{run['id']}/roi", json=FIGURES).status_code == 200
        doc = Document(io.BytesIO(client.get(f"/api/runs/{run['id']}/document").content))
        # In the template's language (Portuguese), after everything else, with a table of the template's style.
        headings = [p.text for p in doc.paragraphs if p.style.name.lower().startswith("heading")]
        assert headings[-2].endswith("Retorno sobre o Investimento (ROI)"), headings
        assert headings[-1].endswith("Viabilidade da Automação"), headings
        last = doc.tables[-1]
        assert [c.text for c in last.rows[0].cells][:1] == ["Item"] and last.rows[1].cells[0].text == "Execuções por mês"


def test_assessing_an_older_project(tmp_path, video):
    """Projects made before the assessment existed get it on request: once, only when ready, only their own."""
    from pipeline import assess_run
    from tests.test_pipeline import FakeLLM
    with make_client(tmp_path) as client:
        register(client)
        client.app.state.assess_run = lambda run_dir: assess_run(run_dir, FakeLLM())
        run = upload(client, video).json()
        run = wait_for(client, run["id"])
        assessment = client.app.state.settings.runs_dir / run["id"] / "assessment.json"
        assert client.post(f"/api/runs/{run['id']}/assess").status_code == 409  # already assessed
        assessment.unlink()  # as a project made before the assessment
        assert client.get(f"/api/runs/{run['id']}").json()["roi"]["assessment"] is None

        assessed = client.post(f"/api/runs/{run['id']}/assess")
        assert assessed.status_code == 200, assessed.text
        assert assessed.json()["roi"]["assessment"]["verdict"] in ("good", "partial", "poor")
        assert assessment.exists()
        assert client.post(f"/api/runs/{run['id']}/assess").status_code == 409

        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.post(f"/api/runs/{run['id']}/assess").status_code == 404


def test_projects_saved_before_a_limit_still_load():
    """Older runs saved more spellings of a name than the model may now write; they load with the first ones."""
    from schemas import load_process_model
    data = {"process_name": "P", "project_proposal": "", "as_is": [],
            "key_names": [{"name": "SAP", "variants": [f"SAP {i}" for i in range(17)]}]}
    model = load_process_model(data)
    assert model.key_names[0].variants == [f"SAP {i}" for i in range(8)]


def test_roi_texts_in_other_languages(tmp_path):
    """Languages not written by hand get the sections translated once per run; a lost placeholder keeps the
    English text, and a failed translation leaves English rather than failing the run."""
    from types import SimpleNamespace
    from roi import RoiTexts, TEXTS, ensure_texts, load_texts, rows, texts_for

    french = {**{k: f"FR {v}" for k, v in TEXTS["en"].items() if isinstance(v, str)},
              "roi": "ROI sur la période", "decimal_comma": True,
              "verdicts": {"good": "Bon candidat", "partial": "En partie", "poor": "Peu adapté"}}
    calls = []

    class FrenchLLM:
        def with_structured_output(self, schema):
            assert schema is RoiTexts
            return SimpleNamespace(invoke=lambda messages: calls.append(1) or RoiTexts(**french))

    texts = ensure_texts(str(tmp_path), "French", FrenchLLM())
    assert texts["title"] == "FR Return on Investment (ROI)" and texts["roi"] == "ROI in {months} months"
    assert ensure_texts(str(tmp_path), "French", FrenchLLM()) == texts and len(calls) == 1  # once per run
    assert ensure_texts(str(tmp_path / "x"), "Português", FrenchLLM()) is None  # written by hand
    assert texts_for("French", load_texts(str(tmp_path)))["verdicts"]["good"] == "Bon candidat"
    table = dict(rows(RoiInputs(**FIGURES), calculate(RoiInputs(**FIGURES), 400), "French", texts))
    assert table["FR Savings per month"] == "R$ 600,00"  # French decimals

    class Broken:
        def with_structured_output(self, schema):
            raise ConnectionError("offline")
    (tmp_path / "y").mkdir()
    assert ensure_texts(str(tmp_path / "y"), "German", Broken()) is None
    assert texts_for("German", None) is TEXTS["en"]
    assert texts_for("Estonian") is TEXTS["en"] and texts_for("es-MX") is TEXTS["es"] and texts_for("pt-PT") is TEXTS["pt"]
    from roi import needs_translation
    assert needs_translation("Estonian") and not needs_translation("") and not needs_translation("Brazilian Portuguese")
