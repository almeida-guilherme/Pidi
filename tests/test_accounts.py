"""The website before more users: log-in limits, privacy and consent, the account's data, retention, fair
scheduling and alerts. Offline, with the fake pipeline, Google and mailer."""
import json
from datetime import timedelta
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlmodel import select

from tests.test_web import (FakeMailer, google_login, login_error, make_client, register, upload,  # noqa: F401
                            video, wait_for)
from web.auth import PRIVACY_VERSION
from sqlalchemy import inspect
from web.db import CreditEntry, Run, User, make_engine, session, utcnow
from web.jobs import JobRunner, delete_recording
from web.mail import Alerts


def test_failed_logins_are_limited(tmp_path):
    with make_client(tmp_path, signup_domains=["company.com"]) as client:
        for _ in range(30):
            assert "@company.com" in login_error(google_login(client, "x@gmail.com"))
        blocked = google_login(client, "ana@company.com")
        assert "Too many attempts" in login_error(blocked)
        assert client.get("/api/auth/me").status_code == 401


def test_privacy_notice_and_upload_consent(tmp_path, video):
    with make_client(tmp_path) as client:
        # New accounts accept the privacy notice before their first upload.
        register(client, accept_privacy=False)
        assert client.get("/api/auth/me").json()["privacy_accepted"] is False
        refused = upload(client, video)
        assert refused.status_code == 403 and "privacy notice" in refused.json()["detail"]
        assert client.post("/api/auth/privacy").json()["privacy_accepted"] is True
        site = client.get("/api/site").json()
        assert site["privacy_version"] == PRIVACY_VERSION and site["retention_days"] == 30

        with open(video, "rb") as f:
            missing = client.post("/api/runs", files={"file": ("x.mp4", f, "video/mp4")})
        assert missing.status_code == 422 and "allowed to upload" in missing.json()["detail"]
        run = upload(client, video).json()
        with session(client.app.state.engine) as db:
            assert db.get(Run, run["id"]).consent_at is not None

        # Accounts that accepted an older notice (or none) are asked again before uploading.
        with session(client.app.state.engine) as db:
            user = db.exec(select(User)).one()
            user.privacy_version = "2020-01-01"
            db.add(user)
            db.commit()
        assert client.get("/api/auth/me").json()["privacy_accepted"] is False
        assert upload(client, video).status_code == 403
        assert client.post("/api/auth/privacy").json()["privacy_accepted"] is True
        assert upload(client, video).status_code == 201


def test_export_and_delete_account(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        run = upload(client, video).json()
        wait_for(client, run["id"])
        exported = client.get("/api/account/export")
        assert "attachment" in exported.headers["content-disposition"]
        data = exported.json()
        assert data["account"]["email"] == "ana@example.com"
        assert data["projects"][0]["recording"] == "Invoice process.mp4" and data["projects"][0]["upload_consent_at"]
        assert data["projects"][0]["credits"] == 2
        assert [(e["kind"], e["amount"]) for e in data["credits"]] == [("grant", 10), ("charge", -2)]

        run_dir = client.app.state.settings.runs_dir / run["id"]
        assert run_dir.exists()
        assert client.request("DELETE", "/api/account", json={"email": "bo@example.com"}).status_code == 403
        assert client.request("DELETE", "/api/account", json={"email": " Ana@Example.com"}).status_code == 204
        assert not run_dir.exists()
        assert client.get("/api/auth/me").status_code == 401
        with session(client.app.state.engine) as db:
            assert db.exec(select(User)).all() == [] and db.exec(select(Run)).all() == []
            assert db.exec(select(CreditEntry)).all() == []
        # The Google account can make a new one.
        register(client)


def test_retention_deletes_the_recording_but_keeps_the_document(tmp_path, video):
    with make_client(tmp_path) as client:
        register(client)
        run = upload(client, video).json()
        wait_for(client, run["id"])
        jobs = client.app.state.jobs
        assert jobs.apply_retention() == 0  # finished just now
        with session(client.app.state.engine) as db:
            record = db.get(Run, run["id"])
            record.finished_at = utcnow() - timedelta(days=31)
            db.add(record)
            db.commit()
        jobs.retention_days = 30
        assert jobs.apply_retention() == 1

        run_dir = client.app.state.settings.runs_dir / run["id"]
        model = json.loads((run_dir / "process_model.json").read_text())
        kept = {Path(s["main_frame_path"]).name for s in model["as_is"] if s["main_frame_path"]}
        assert not (run_dir / "video.mp4").exists()
        assert {p.name for p in (run_dir / "frames").glob("*.jpg")} == kept  # only the document's screenshots
        assert client.get(f"/api/runs/{run['id']}/document").status_code == 200
        shown = client.get(f"/api/runs/{run['id']}").json()
        assert shown["recording_deleted_at"] and shown["status"] == "done"
        assert jobs.apply_retention() == 0  # done once


def test_delete_recording_without_model(tmp_path):
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "F001.jpg").write_bytes(b"x")
    (tmp_path / "video.mp4").write_bytes(b"x")
    delete_recording(tmp_path, "video.mp4")  # a run that failed before the steps: everything goes
    assert list(tmp_path.rglob("*.jpg")) == [] and not (tmp_path / "video.mp4").exists()


def test_runs_take_turns_between_people(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'app.db'}")
    with session(engine) as db:
        ana, bo = User(email="ana@x.com"), User(email="bo@x.com")
        db.add_all([ana, bo])
        db.commit()
        start = utcnow()
        for n, user in enumerate([ana, ana, ana, bo]):  # Ana queued three projects before Bo's
            db.add(Run(id=f"r{n}", user_id=user.id, video_name="v.mp4", video_file="v.mp4",
                       created_at=start + timedelta(seconds=n)))
        db.commit()
        ana_id, bo_id = ana.id, bo.id
    runner = JobRunner(engine, tmp_path, workers=2, runs_per_user=1)
    first, second = runner._next_run(), runner._next_run()
    assert (first.user_id, second.user_id) == (ana_id, bo_id)  # Bo doesn't wait for all of Ana's
    assert runner._next_run() is None  # Ana's next one waits for her first


def test_failed_runs_and_server_errors_are_reported(tmp_path, video):
    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    with make_client(tmp_path, run_fn=broken, alert_email="admin@example.com") as client:
        mailer = client.app.state.mailer
        register(client)
        run = upload(client, video).json()
        assert wait_for(client, run["id"])["status"] == "failed"
        alert = next(body for to, subject, body in mailer.sent if to == "admin@example.com")
        assert "disk full" in alert and run["id"] in alert and "ana@example.com" in alert
        # The same kind of failure isn't reported again right away.
        run = upload(client, video).json()
        wait_for(client, run["id"])
        assert [to for to, _, _ in mailer.sent].count("admin@example.com") == 1


def test_server_errors_are_answered_and_reported(tmp_path, monkeypatch):
    def broken(db, user_id):
        raise RuntimeError("database gone")

    with make_client(tmp_path, alert_email="admin@example.com") as client:
        register(client)
        monkeypatch.setattr("web.credits.balance", broken)
        response = client.get("/api/credits")
        assert response.status_code == 500 and "Something went wrong" in response.json()["detail"]
        assert response.headers["x-frame-options"] == "DENY"  # the answer to an error has the headers too
        alert = next(body for to, _, body in client.app.state.mailer.sent if to == "admin@example.com")
        assert "GET /api/credits failed" in alert and "database gone" in alert


def test_alerts_are_quieted():
    mailer = FakeMailer()
    alerts = Alerts(mailer, "admin@example.com", quiet_minutes=15)
    alerts.report("a", "one", "x")
    alerts.report("a", "two", "x")
    alerts.report("b", "three", "x")
    assert [s for _, s, _ in mailer.sent] == ["[Pidi] one", "[Pidi] three"]
    Alerts(mailer, "").report("a", "nobody to tell", "x")
    assert len(mailer.sent) == 2


def test_health(tmp_path):
    with make_client(tmp_path) as client:
        health = client.get("/api/health")
        assert health.status_code == 200 and health.json() == {"ok": True, "workers": 2, "queued": 0, "running": 0}


def test_databases_from_earlier_versions_keep_working(tmp_path):
    """Accounts from the email-and-password version: they log in with the Google account of their email."""
    url = f"sqlite:///{tmp_path / 'old.db'}"
    old = create_engine(url)
    with old.begin() as conn:
        conn.execute(text('CREATE TABLE user (id INTEGER PRIMARY KEY, email VARCHAR NOT NULL, password_hash VARCHAR '
                          'NOT NULL, name VARCHAR, session_version INTEGER, created_at DATETIME NOT NULL)'))
        conn.execute(text("INSERT INTO user (email, password_hash, created_at) VALUES ('a@x.com', '-', "
                          "'2026-01-02 03:04:05')"))
    engine = make_engine(url)
    assert inspect(engine).has_table("apikey")  # people's own keys (billing "keys"), kept again
    with session(engine) as db:
        user = db.exec(select(User)).one()
        assert user.google_sub is None and user.privacy_version is None
        db.add(User(email="new@x.com"))  # no password: the old NOT NULL column gets ""
        db.commit()
