"""Phase 5's evaluation: scoring steps against a case's expected steps, making cases, and a run (offline)."""
import json

from evaluation.make_case import make_case
from evaluation.run import run
from evaluation.score import score
from tests.test_pipeline import FakeLLM
from tests.test_web import make_client, register, upload, video, wait_for  # noqa: F401

STEPS = [
    {"number": 1, "instruction": "Open the intranet and log in", "application": "Intranet", "frame_ids": ["F001", "F002"]},
    {"number": 2, "instruction": "Search for the client by name", "application": "Intranet", "frame_ids": ["F003", "F004"]},
    {"number": 3, "instruction": "Click Save", "application": "Intranet", "frame_ids": ["F005"]},
]
CASE = {"expected": {"steps": STEPS, "applications": ["Intranet"]}}


def test_scores():
    perfect = score(CASE, STEPS)
    assert (perfect["f1"], perfect["found"], perfect["extra"], perfect["missed"]) == (1.0, 1.0, 0.0, [])

    # Reworded but on the same frames: still the same steps.
    reworded = [{**s, "instruction": s["instruction"].replace("Open", "Go to")} for s in STEPS]
    assert score(CASE, reworded)["f1"] == 1.0

    # A missed step and a split one.
    missed = score(CASE, STEPS[:2])
    assert missed["found"] == round(2 / 3, 3) and missed["missed"] == [3] and missed["extra"] == 0
    split = STEPS[:1] + [
        {"number": 2, "instruction": "Type the client's name in the search field", "frame_ids": ["F003"]},
        {"number": 3, "instruction": "Press Search", "frame_ids": ["F004"]}] + [dict(STEPS[2], number=4)]
    result = score(CASE, split)
    assert result["found"] == 1.0 and result["extra"] == 0.25 and result["steps"] == 4
    assert score(CASE, [{**s, "application": "SAP"} for s in STEPS])["applications"] == 0.0


def test_a_case_from_a_project_and_a_run(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        project = wait_for(client, upload(client, video).json()["id"])
        run_dir = client.app.state.settings.runs_dir / project["id"]
        folder = make_case(run_dir, "Invoices", cases=tmp_path / "cases")
    case = json.loads((folder / "case.json").read_text())
    assert case["name"] == "Invoices" and not case["approved"]
    assert len(case["expected"]["steps"]) == len(json.loads((run_dir / "process_model.json").read_text())["as_is"])
    assert (folder / "captions.json").exists() and not list(folder.glob("video*"))  # never the recording

    lines = []
    result = run([folder], FakeLLM(), repeats=2, progress=lines.append)
    runs = result["cases"][0]["runs"]
    assert len(runs) == 2 and len(lines) == 2
    assert runs[0]["f1"] > 0.5 and result["summary"]["runs"] == 2 and result["summary"]["failed"] == 0
