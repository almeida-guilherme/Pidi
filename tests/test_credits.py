"""Credits: projects are charged at upload by the recording's length, and given back only when nothing was spent
on them (deleted before processing) or they stop at their cost limit. Offline, with the fake pipeline and Google; the synthetic recording is 1 min 44 s, so it costs 2."""
import sys
import threading
from datetime import timedelta
from sqlmodel import select

from costs import CostLimitReached
from tests.test_web import fake_run, google_login, make_client, register, upload, video, wait_for  # noqa: F401
from web import create_user, grant_credits
from web.credits import NotEnoughCredits, add_charged_run, grant
from web.db import CreditEntry, Run, User, make_engine, session, utcnow


def credits(client) -> dict:
    return client.get("/api/credits").json()


def user_id(client) -> int:
    return client.get("/api/auth/me").json()["id"]


def test_projects_are_paid_with_credits(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        assert credits(client) == {"balance": 10, "entries": [
            {**credits(client)["entries"][0], "amount": 10, "kind": "grant", "note": "Welcome credits"}]}
        site = client.get("/api/site").json()["credits"]
        assert site == {"per_minute": 1, "translation": 1, "signup": 10}

        # The price, before uploading: per started minute, plus translating Pidi's template the first time.
        def quote(**params):
            return client.get("/api/credits/quote", params=params)
        assert quote(seconds=104, language="en").json() == {"credits": 2, "balance": 10}
        assert quote(seconds=104, language="Spanish").json()["credits"] == 3
        assert quote(seconds=104).json()["credits"] == 2  # a company template
        assert quote(seconds=0, language="pt-BR").json()["credits"] == 1
        assert quote(seconds=-1).status_code == 422 and quote(seconds=10, language="<b>").status_code == 422

        with open(video, "rb") as f:
            run = client.post("/api/runs", files={"file": ("x.mp4", f, "video/mp4")},
                              data={"consent": "true", "name": "Invoice approval"}).json()
        assert run["credits"] == 2 and credits(client)["balance"] == 8
        run = wait_for(client, run["id"])
        assert run["status"] == "done" and run["name"] == "Invoice approval"
        assert "Invoice%20approval%20-%20PDD.docx" in client.get(
            f"/api/runs/{run['id']}/document").headers["content-disposition"]
        charge = credits(client)["entries"][0]
        assert (charge["kind"], charge["amount"], charge["run_id"], charge["note"]) == (
            "charge", -2, run["id"], "Invoice approval")

        # Not enough credits: refused before anything is spent, and nothing is kept.
        grant(client.app.state.engine, user_id(client), -7, "test")
        refused = upload(client, video)
        assert refused.status_code == 402 and refused.json()["detail"] == "This recording needs 2 credits and you have 1."
        assert credits(client)["balance"] == 1 and len(client.get("/api/runs").json()) == 1
        assert [p.name for p in client.app.state.settings.runs_dir.iterdir()] == [run["id"]]

        # Deleting a project that made its document gives nothing back.
        assert client.delete(f"/api/runs/{run['id']}").status_code == 204
        assert credits(client)["balance"] == 1


def test_failed_projects_are_retried_for_free(tmp_path, video):
    """Retrying a failed project is free; deleting it gives nothing back, since OpenAI was already paid for it."""
    attempts = []

    def flaky(video_path, run_dir, progress, **kwargs):
        attempts.append(run_dir)
        if len(attempts) in (1, 3):
            raise RuntimeError("Error code: 429 - insufficient_quota")
        return fake_run(video_path, run_dir, progress, **kwargs)

    with make_client(tmp_path, run_fn=flaky) as client:
        register(client)
        first = wait_for(client, upload(client, video).json()["id"])
        assert first["status"] == "failed" and credits(client)["balance"] == 8
        client.post(f"/api/runs/{first['id']}/retry")
        assert wait_for(client, first["id"])["status"] == "done" and credits(client)["balance"] == 8

        second = wait_for(client, upload(client, video).json()["id"])
        assert second["status"] == "failed" and credits(client)["balance"] == 6
        assert client.delete(f"/api/runs/{second['id']}").status_code == 204
        assert credits(client)["balance"] == 6
        assert credits(client)["entries"][0]["kind"] == "charge"


def test_deleting_a_project_before_it_runs_gives_the_credits_back(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        client.app.state.jobs.stop()  # it stays queued
        run = upload(client, video).json()
        assert credits(client)["balance"] == 8
        assert client.delete(f"/api/runs/{run['id']}").status_code == 204
        assert credits(client)["balance"] == 10
        refund = credits(client)["entries"][0]
        assert refund["kind"] == "refund" and refund["amount"] == 2
        assert refund["note"] == "Invoice process.mp4: deleted before it was processed"


def test_cost_limit_gives_the_credits_back(tmp_path, video):
    limits = []

    def expensive(video_path, run_dir, progress, max_cost_usd=None, **_):
        limits.append(max_cost_usd)
        raise CostLimitReached(f"The run cost $9.00, over its limit of ${max_cost_usd:.2f}.")

    with make_client(tmp_path, run_fn=expensive, max_cost_per_credit_usd=0.4) as client:
        register(client)
        run = wait_for(client, upload(client, video).json()["id"])
        assert limits == [0.8]  # 2 credits × $0.40
        assert run["status"] == "failed" and run["refunded"] and "your 2 credits were given back" in run["error"]
        assert credits(client)["balance"] == 10
        refused = client.post(f"/api/runs/{run['id']}/retry")  # it would only stop again
        assert refused.status_code == 409 and "given back" in refused.json()["detail"]
        assert client.delete(f"/api/runs/{run['id']}").status_code == 204
        assert credits(client)["balance"] == 10  # given back once


def test_retention_ends_the_free_retries_of_failed_projects(tmp_path, video):
    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    with make_client(tmp_path, run_fn=broken) as client:
        register(client)
        run = wait_for(client, upload(client, video).json()["id"])
        with session(client.app.state.engine) as db:
            record = db.get(Run, run["id"])
            record.finished_at = utcnow() - timedelta(days=31)
            db.add(record)
            db.commit()
        jobs = client.app.state.jobs
        jobs.retention_days = 30
        assert jobs.apply_retention() == 1
        assert credits(client)["balance"] == 8  # it was processed, so OpenAI was paid
        assert client.post(f"/api/runs/{run['id']}/retry").status_code == 409  # its recording is gone


def test_uploads_at_once_cant_spend_the_same_credits(tmp_path):
    with make_client(tmp_path) as client:
        register(client)
        engine, uid = client.app.state.engine, user_id(client)
        grant(engine, uid, -5, "test")  # 5 left: two projects of 2, not three
        results = []

        def charge():
            try:
                add_charged_run(engine, Run(user_id=uid, video_name="x.mp4", video_file="video.mp4",
                                            status="failed"), 2)
                results.append("charged")
            except NotEnoughCredits:
                results.append("refused")

        threads = [threading.Thread(target=charge) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == ["charged"] * 2 + ["refused"] * 4
        assert credits(client)["balance"] == 1


def test_projects_from_before_credits_dont_run_for_free(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        client.app.state.jobs.stop()
        run_id = upload(client, video).json()["id"]
        with session(client.app.state.engine) as db:  # as an earlier version left it
            run = db.get(Run, run_id)
            run.credits = None
            db.add(run)
            db.commit()
    with make_client(tmp_path) as client:
        google_login(client)
        run = wait_for(client, run_id)
        assert run["status"] == "failed" and "before Pidi used credits" in run["error"]
        assert client.post(f"/api/runs/{run_id}/retry").status_code == 409


def test_administrator_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PDD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PDD_SIGNUP_CREDITS", "5")
    monkeypatch.setenv("PDD_BILLING", "credits")

    def run(module, *args):
        monkeypatch.setattr(sys, "argv", ["x", *args])
        module.main()
        return capsys.readouterr().out

    assert "with 5 credits" in run(create_user, "ana@example.com")
    assert run(grant_credits, "Ana@example.com", "30", "October") == "ana@example.com now has 35 credits.\n"
    assert run(grant_credits, "ana@example.com", "-10") == "ana@example.com now has 25 credits.\n"
    assert run(grant_credits, "ana@example.com") == "ana@example.com has 25 credits.\n"
    with session(make_engine(f"sqlite:///{tmp_path / 'data' / 'app.db'}")) as db:
        ana = db.exec(select(User)).one()
        notes = [e.note for e in db.exec(select(CreditEntry).where(CreditEntry.user_id == ana.id))]
    assert notes == ["Welcome credits", "October", "Given by the administrator"]
