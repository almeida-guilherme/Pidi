"""The automation spec: a finished run written as Markdown (and its screenshots) for an agent that builds the
automation; from the run's saved results, no model calls."""
import io
import json
import zipfile

from spec import SPEC_VERSION
from tests.test_web import make_client, register, upload, video, wait_for  # noqa: F401


def spec_of(client, run_id) -> tuple[dict, str, list[str]]:
    response = client.get(f"/api/runs/{run_id}/automation")
    assert response.status_code == 200, response.text
    z = zipfile.ZipFile(io.BytesIO(response.content))
    text = z.read("automation.md").decode()
    _, front, body = text.split("---\n", 2)
    # The front matter's values are JSON, so a YAML reader and a JSON one both read it.
    meta = {key: json.loads(value) for key, value in (line.split(": ", 1) for line in front.strip().splitlines())}
    return meta, body, z.namelist()


def test_automation_spec(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        client.app.state.jobs.stop()
        queued = upload(client, video).json()
        assert client.get(f"/api/runs/{queued['id']}/automation").status_code == 409  # not made yet
    with make_client(tmp_path) as client:
        register(client)
        run = wait_for(client, queued["id"])
        # What a frame showed is the agent's best hint for finding fields and buttons.
        captions_file = tmp_path / "data" / "runs" / run["id"] / "captions.json"
        captions = json.loads(captions_file.read_text())
        for c in captions:
            c["visible_text"] = ["Client name", "Save", "client name"]
        captions_file.write_text(json.dumps(captions))

        meta, body, files = spec_of(client, run["id"])
        assert meta["spec_version"] == SPEC_VERSION and meta["process"] == "Create invoice"
        assert meta["applications"] == ["Intranet"] and meta["decisions"] == [4] and meta["steps"] == 12
        assert meta["inputs"] == [{"name": "Client list", "kind": "structured", "source": "Intranet"}]
        assert (meta["minutes_per_run"], meta["minutes_per_run_source"]) == (0.5, "estimated")
        assert meta["runs_per_month"] is None and meta["viability"] == "good"
        assert "automation.md" in files and "screenshots/S1.jpg" in files and "screenshots/S2.jpg" not in files
        assert "### S1 · Intranet · 00:00" in body and "[screenshots/S1.jpg](screenshots/S1.jpg)" in body
        assert '- **Visible on screen:** "Client name", "Save"\n' in body  # once each
        assert "- **Decision:** If the client exists, open it; otherwise create it." in body
        assert "- `Report 5.5.26` (not: Report 5.3.26, Report)" in body
        assert "| No client | Client missing |  | Skip |" in body
        assert "**good**: Rule-based steps on one intranet." in body
        assert "- How often does the process run (runs per month), and how many items per run?" in body
        assert "- Steps without a screenshot (S2): which screen are they done on?" in body
        # The spec describes the process, not a technology.
        assert not any(word in body.lower() for word in ("playwright", "uipath", "selenium", "python"))

        preview = client.get(f"/api/runs/{run['id']}/automation/preview").json()
        assert preview["markdown"].split("---\n", 2)[2] == body and preview["screenshots"] == 11
        assert preview["open_questions"] == 3

        # It follows the project's ROI figures, and the frequency stops being an open question.
        client.put(f"/api/runs/{run['id']}/roi", json={"runs_per_month": 120, "minutes_per_run": 4})
        meta, body, _ = spec_of(client, run["id"])
        assert (meta["runs_per_month"], meta["runs_per_month_source"]) == (120, "given")
        assert (meta["minutes_per_run"], meta["minutes_per_run_source"]) == (4, "given")
        assert "How often does the process run" not in body

        # Only its owner gets it.
        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.get(f"/api/runs/{run['id']}/automation").status_code == 404
