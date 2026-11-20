"""What people spend on their AI provider: estimated per action and added up on the Costs page (web/usage.py)."""
import json
from datetime import timedelta

import pytest
from sqlmodel import select

from costs import cost_by_model, price_for
from tests.test_web import make_client, register, upload, video, wait_for  # noqa: F401
from web.db import Usage, session, utcnow


def test_prices_per_provider():
    usage = {"claude-opus-5-5": {"input_tokens": 1_000_000, "output_tokens": 100_000,
                                 "input_token_details": {"cache_read": 200_000}},
             "gpt-4.1-mini-2025-04-14": {"input_tokens": 1_000_000, "output_tokens": 0},
             "my-own-model": {"input_tokens": 10, "output_tokens": 10}}
    costs, unknown = cost_by_model(usage)
    assert costs["claude-opus-5-5"] == pytest.approx(0.8 * 4 + 0.2 * 0.2 + 0.1 * 20)
    assert costs["gpt-4.1-mini-2025-04-14"] == pytest.approx(0.40)  # dated snapshots use their model's price
    assert unknown == ["my-own-model"]
    assert cost_by_model(usage, free=True) == ({m: 0.0 for m in usage}, [])  # the server's Ollama
    assert price_for("gemini-3.5-flash-lite") and price_for("gemini-3.8-flash") and price_for("claude-haiku-5-5")


def test_costs_page(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        run = wait_for(client, upload(client, video).json()["id"])
        engine = client.app.state.engine
        with session(engine) as db:
            rows = db.exec(select(Usage)).all()
        # The attempt was recorded (the fake model reports no tokens, so it cost nothing).
        assert [(r.kind, r.run_id, r.cost_usd) for r in rows] == [("project", run["id"], 0.0)]
        costs = client.get("/api/costs").json()
        assert costs["projects"][0]["run_id"] == run["id"]

        # Other actions, this month and last month, by provider.
        user_id = rows[0].user_id
        now = utcnow()
        with session(engine) as db:
            db.add(Usage(user_id=user_id, run_id=run["id"], kind="redo", provider="anthropic", cost_usd=0.25,
                         by_model=json.dumps({"claude-opus-5-5": 0.25})))
            db.add(Usage(user_id=user_id, kind="template", provider="openai", cost_usd=0.05,
                         by_model=json.dumps({"gpt-4.1": 0.05}), note="Company PDD"))
            db.add(Usage(user_id=user_id, run_id="gone", kind="project", provider="openai", cost_usd=1.0,
                         by_model=json.dumps({"gpt-4.1": 1.0}), created_at=now.replace(day=1) - timedelta(days=1)))
            db.commit()
        costs = client.get("/api/costs").json()
        assert costs["this_month"] == pytest.approx(0.30) and costs["last_month"] == pytest.approx(1.0)
        assert costs["total"] == pytest.approx(1.30) and costs["templates"] == pytest.approx(0.05)
        assert costs["months"][-1]["by_provider"] == {"server": 0.0, "anthropic": 0.25, "openai": 0.05}
        assert len(costs["months"]) == 12 and costs["months"][-2]["by_provider"] == {"openai": 1.0}
        assert costs["by_model"]["gpt-4.1"] == pytest.approx(1.05)
        project = costs["projects"][0]  # a deleted project counts in the totals, not in the list
        assert len(costs["projects"]) == 1 and project["by_kind"]["redo"] == 0.25 and project["total"] == 0.25

        # Others don't see it.
        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.get("/api/costs").json()["total"] == 0


def test_projects_from_before_get_their_cost(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        run = wait_for(client, upload(client, video).json()["id"])
        engine = client.app.state.engine
        with session(engine) as db:
            for row in db.exec(select(Usage)).all():
                db.delete(row)
            db.commit()
        report_path = client.app.state.settings.runs_dir / run["id"] / "report.json"
        report = json.loads(report_path.read_text())
        report.pop("attempt", None)
        report.update(cost_usd=0.5, transcription_cost_usd=0.05, tokens_by_model={"gpt-4.1": 300, "gpt-4.1-mini": 600},
                      recorded_seconds=300)
        report_path.write_text(json.dumps(report))
        costs = client.get("/api/costs").json()
        assert costs["total"] == pytest.approx(0.5)
        assert costs["by_model"] == {"gpt-4.1-mini": pytest.approx(0.3), "gpt-4.1": pytest.approx(0.15),
                                     "whisper-1": pytest.approx(0.05)}
        assert costs["projects"][0]["minutes"] == 5 and costs["per_minute"] == pytest.approx(0.1)
        client.get("/api/costs")  # only once
        with session(engine) as db:
            assert len(db.exec(select(Usage)).all()) == 1


def test_estimate_before_uploading(tmp_path, video, monkeypatch):
    """Roughly what a recording will cost on the key it will run on: from the person's own projects on the same
    models when there are some, otherwise a typical project priced for those models."""
    with make_client(tmp_path, billing="keys") as client:
        register(client)
        openai = client.post("/api/keys", json={"key": "sk-test-1234567890abcdefWXYZ"}).json()
        typical = client.get("/api/costs/estimate", params={"seconds": 600}).json()
        assert typical["basis"] == "typical" and typical["mid"] == pytest.approx(0.99, abs=0.02)  # ~$0.10 a minute
        assert typical["low"] < typical["mid"] < typical["high"]

        claude = client.post("/api/keys", json={"provider": "anthropic", "key": "sk-ant-test-1234567890abWXYZ",
                                                "model": "claude-haiku-5-5"}).json()
        cheap = client.get("/api/costs/estimate", params={"seconds": 600, "api_key_id": claude["id"]}).json()
        assert cheap["mid"] < typical["mid"] / 5
        mine = client.post("/api/keys", json={"provider": "anthropic", "key": "sk-ant-test-1234567890abQRST",
                                              "model": "my-model"}).json()
        unknown = client.get("/api/costs/estimate", params={"seconds": 600, "api_key_id": mine["id"]}).json()
        assert unknown["basis"] == "unknown" and unknown["mid"] is None and unknown["unpriced"] == ["my-model"]
        monkeypatch.setenv("PDD_OLLAMA_URL", "http://ollama.internal:11434")
        local = client.post("/api/keys", json={"provider": "ollama"}).json()
        assert client.get("/api/costs/estimate", params={"seconds": 600, "api_key_id": local["id"]}).json()["basis"] == "free"

        # After a project on the same models: from its real cost.
        run = wait_for(client, upload(client, video, api_key_id=openai["id"]).json()["id"])
        report_path = client.app.state.settings.runs_dir / run["id"] / "report.json"
        report = json.loads(report_path.read_text())
        report.update(caption_model="gpt-4.1-mini-2025-04-14", structure_model="gpt-4.1-2025-04-14", recorded_seconds=300)
        report_path.write_text(json.dumps(report))
        with session(client.app.state.engine) as db:
            row = db.exec(select(Usage)).one()
            row.cost_usd, row.provider = 0.75, "openai"
            db.add(row)
            db.commit()
        history = client.get("/api/costs/estimate", params={"seconds": 600, "api_key_id": openai["id"]}).json()
        assert history["basis"] == "history" and history["projects"] == 1 and history["mid"] == pytest.approx(1.5)
