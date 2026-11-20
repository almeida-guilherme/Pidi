"""Doing a project again from its review notes (revise.py, web/reviews.py), with a fake model."""
import json
import time
from types import SimpleNamespace

import revise
from revise import NoteAnswer, Revision, RevisedStep, StepChange
from tests.test_web import make_client, register, upload, video, wait_for  # noqa: F401


class FakeReviser:
    """Answers with a fixed revision (and, asked to assess the process again, a fixed assessment) and keeps what it
    was sent."""

    def __init__(self, revision: Revision | Exception, assessment=None):
        self.revision, self.assessment, self.sent = revision, assessment, []

    def with_structured_output(self, schema):
        if schema is not Revision:
            if self.assessment is None:
                raise RuntimeError("no assessment")
            return SimpleNamespace(invoke=lambda messages: self.assessment)
        return SimpleNamespace(invoke=self._invoke)

    def _invoke(self, messages):
        self.sent.append(messages)
        if isinstance(self.revision, Exception):
            raise self.revision
        return self.revision


def made(client, video):
    run = wait_for(client, upload(client, video).json()["id"])
    assert run["status"] == "done"
    run_dir = client.app.state.settings.runs_dir / run["id"]
    model = json.loads((run_dir / "process_model.json").read_text())
    return run, run_dir, model


def frames_of(model, number):
    return next(s for s in model["as_is"] if s["number"] == number)["frame_ids"]


def test_notes_change_only_the_marked_steps(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        run, run_dir, before = made(client, video)
        steps = before["as_is"]
        assert len(steps) >= 6
        f3 = frames_of(before, 3)
        from assess import Assessment
        assessed = json.loads((run_dir / "assessment.json").read_text())
        reviser = FakeReviser(assessment=Assessment.model_validate({**assessed, "summary": "Assessed again."}),
                              revision=Revision(
            changes=[
                StepChange(action="replace", step=1, new_steps=[  # split in two
                    RevisedStep(instruction="Open the intranet", short_label="Open", frame_ids=steps[0]["frame_ids"][:1]),
                    RevisedStep(instruction="Log in as the clerk", short_label="Log in", frame_ids=steps[0]["frame_ids"][1:],
                                main_frame_id=(steps[0]["frame_ids"][1:] or [None])[0])]),
                StepChange(action="insert_after", step=3, new_steps=[
                    RevisedStep(instruction="Click Save", short_label="Save", frame_ids=f3[-1:])]),
                StepChange(action="delete", step=5),
                StepChange(action="delete", step=999),  # no such step: ignored
            ],
            answers=[NoteAnswer(note=1, outcome="fixed", explanation="Split into opening and logging in."),
                     NoteAnswer(note=2, outcome="fixed", explanation="Added the save."),
                     NoteAnswer(note=3, outcome="not_in_recording", explanation="The recording shows no approval.")]))
        feedback = {"general": "", "steps": [{"number": 1, "issue": "split", "note": ""},
                                              {"number": 3, "issue": "missing_after", "note": "Click Save"},
                                              {"number": 5, "issue": "other", "note": "Approval missing"}]}
        result = revise.redo(str(run_dir), feedback, 1, llm=reviser)

        after = json.loads((run_dir / "process_model.json").read_text())["as_is"]
        assert [s["number"] for s in after] == list(range(1, len(steps) + 2))  # +1 split, +1 inserted, -1 deleted
        assert [s["instruction"] for s in after[:2]] == ["Open the intranet", "Log in as the clerk"]
        assert after[4]["instruction"] == "Click Save"
        # Steps nobody marked stay exactly as they were (only renumbered).
        assert after[2]["instruction"] == steps[1]["instruction"] and after[3]["instruction"] == steps[2]["instruction"]
        assert after[5]["instruction"] == steps[3]["instruction"]
        assert all(s["instruction"] != steps[4]["instruction"] for s in after)  # step 5 deleted
        assert result["changed"] == [1, 2, 5]
        assert result["answers"][2] == {"label": "Step 5", "outcome": "not_in_recording",
                                        "explanation": "The recording shows no approval."}
        # The version before is kept, the document and the page's figures follow the new steps.
        kept = json.loads((run_dir / "versions" / "1" / "process_model.json").read_text())
        assert kept["as_is"] == steps
        report = json.loads((run_dir / "report.json").read_text())
        assert report["steps"] == len(after)
        assert report["decision_points"] == [s["number"] for s in after if s.get("decision")]
        assert client.get(f"/api/runs/{run['id']}").json()["summary"]["steps"] == len(after)

        # The model got the notes, the step list and the marked steps' screenshots.
        text = reviser.sent[0][1].content[0]["text"]
        assert "Note 2. Step 3: a step is missing right after this one" in text and "Click Save" in text
        assert any(part.get("type") == "image_url" for part in reviser.sent[0][1].content)

        # The assessment was written from the steps before: it's done again from the corrected ones.
        assert json.loads((run_dir / "assessment.json").read_text())["summary"] == "Assessed again."

        revise.restore_version(str(run_dir), 1)
        assert json.loads((run_dir / "process_model.json").read_text())["as_is"] == steps
        assert json.loads((run_dir / "assessment.json").read_text()) == assessed  # undone with the steps


def test_redo_from_the_project_page(tmp_path, video):
    calls = []

    def redo_run(run_dir, feedback, version, api_key=None):
        calls.append(feedback)
        if "fail" in feedback["general"]:
            raise ConnectionError("network down")
        changes = [StepChange(action="replace", step=2, new_steps=[RevisedStep(instruction="Better step 2", short_label="Two")])]
        return revise.redo(run_dir, feedback, version, llm=FakeReviser(Revision(
            changes=changes, answers=[NoteAnswer(note=1, outcome="fixed", explanation="Reworded.")])))

    with make_client(tmp_path) as client:
        client.app.state.redo_run = redo_run
        register(client)
        run, run_dir, before = made(client, video)
        url = f"/api/runs/{run['id']}/review"
        assert client.post(f"{url}/redo").status_code == 409  # no notes yet
        client.put(f"{url}/feedback", json={"steps": [{"number": 2, "issue": "wording", "note": "Say which button"}]})

        def finished():
            for _ in range(100):
                review = client.get(url).json()
                if review["status"] != "redoing":
                    return review
                time.sleep(0.1)
            raise AssertionError("the redo didn't finish")

        started = client.post(f"{url}/redo")
        assert started.status_code == 200 and started.json()["status"] == "redoing"
        review = finished()
        assert review["status"] == "pending" and review["version"] == 2 and review["feedback"]["steps"] == []
        version = review["versions"][0]
        assert version["changed"] == [2] and version["answers"][0]["label"] == "Step 2"
        assert version["notes"]["steps"][0]["note"] == "Say which button"
        redone = client.get(f"/api/runs/{run['id']}/model").json()["as_is"][1]
        assert redone["instruction"] == "Better step 2"
        # Reworded without frames, it keeps its own (and its screenshot).
        assert redone["frame_ids"] == before["as_is"][1]["frame_ids"]
        original = before["as_is"][1]["main_frame_path"]
        assert (redone["image_url"] or "").endswith(original.rsplit("/", 1)[-1].removesuffix(".jpg")) if original \
            else redone["image_url"] is None
        assert review["redos_left"] == 1  # credits: two redos per project

        # Undo: the version before, with the notes it was made from.
        undone = client.post(f"{url}/undo").json()
        assert undone["version"] == 1 and undone["status"] == "changes_requested"
        assert undone["feedback"]["steps"][0]["note"] == "Say which button"
        assert client.get(f"/api/runs/{run['id']}/model").json()["as_is"][1]["instruction"] == before["as_is"][1]["instruction"]
        assert undone["redos_left"] == 1  # an undone redo still counts

        # A failed redo changes nothing and says why.
        client.put(f"{url}/feedback", json={"general": "please fail"})
        client.post(f"{url}/redo")
        failed = finished()
        assert failed["status"] == "changes_requested" and failed["version"] == 1 and failed["redo_error"]
        unchanged = client.get(f"/api/runs/{run['id']}/model").json()["as_is"]
        assert [s["instruction"] for s in unchanged] == [s["instruction"] for s in before["as_is"]]

        # The second (and last) redo with credits, then no more.
        client.put(f"{url}/feedback", json={"steps": [{"number": 2, "issue": "wording"}]})
        client.post(f"{url}/redo")
        assert finished()["redos_left"] == 0
        client.put(f"{url}/feedback", json={"steps": [{"number": 1, "issue": "wording"}]})
        refused = client.post(f"{url}/redo")
        assert refused.status_code == 409 and "2 times" in refused.json()["detail"]

        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.post(f"{url}/redo").status_code == 404 and client.post(f"{url}/undo").status_code == 404


def test_redos_with_own_keys_have_no_limit(tmp_path, video):
    with make_client(tmp_path, billing="keys") as client:
        register(client)
        client.post("/api/keys", json={"key": "sk-test-1234567890abcdefWXYZ"})
        run, _, _ = made(client, video)
        assert client.get(f"/api/runs/{run['id']}/review").json()["redos_left"] is None
