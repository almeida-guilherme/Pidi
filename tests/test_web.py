"""The website's API, offline: the pipeline runs with the fake model on the synthetic recording."""
import io
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit
import pytest
from docx import Document
from fastapi.testclient import TestClient

from pipeline import run_pdd_pipeline
from tests.synthetic_video import build
from tests.template_fixture import build_template
from tests.test_pipeline import FakeLLM
from tests.test_template import FakeTemplateLLM, analysis_for_fixture
from web.app import create_app
from web.config import Settings
from web.db import Run, User, session


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("video") / "synthetic.mp4"
    build(str(path))
    return path


def fake_run(video_path, run_dir, progress, language="en", translations_dir=None, **_):
    llm = FakeLLM()
    return run_pdd_pipeline(video_path, run_dir, llm=llm, structure_llm=llm, transcribe=lambda v, audio_dir: [],
                            progress=progress, language=language, translations_dir=translations_dir)


def fake_template_run(video_path, run_dir, progress, **_):
    llm = FakeTemplateLLM()
    return run_pdd_pipeline(video_path, run_dir, llm=llm, structure_llm=llm, transcribe=lambda v, audio_dir: [],
                            progress=progress)


def make_client(tmp_path, run_fn=fake_run, create_checkout=None, **settings) -> TestClient:
    # Set here, so the developer's .env (which create_app loads) doesn't change what the tests see. Most tests
    # use credits; tests/test_keys.py uses people's own keys.
    settings = {"google_client_id": "test-client", "google_client_secret": "test-secret", "signup_credits": 10,
                "credits_per_minute": 1, "max_cost_per_credit_usd": 0.15, "asaas_api_key": "",
                "asaas_webhook_token": "", "billing": "credits", "allow_server_key": False, "local_login": False,
                **settings}
    s = Settings(data_dir=tmp_path / "data", frontend_dir=tmp_path / "no-frontend", **settings)
    return TestClient(create_app(s, run_fn=run_fn, analyze_template=lambda path, **_: analysis_for_fixture(),
                                 mailer=FakeMailer(), google_identity=fake_google,
                                 create_checkout=create_checkout or no_checkout, check_api_key=fake_check_key))


def fake_check_key(provider: str, key: str) -> None:
    """The provider's answer to a key, offline: keys with "bad" in them are refused."""
    if "bad" in key:
        raise ValueError(f"{provider} refused this key. Check that it was copied whole and is still active.")


def no_checkout(*args):
    raise AssertionError("tests that buy credits pass their own create_checkout")


def fake_google(code, redirect_uri, client_id, client_secret):
    """Google's ID token claims; in tests the code is the claims themselves, as JSON."""
    assert redirect_uri.endswith("/api/auth/google/callback") and client_id == "test-client"
    claims = json.loads(code)
    return {"sub": f"google-{claims['email'].lower()}", "email_verified": True, **claims}


class FakeMailer:
    """Keeps the emails instead of sending them."""
    configured = True

    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))


def google_login(client, email="ana@example.com", next=None, **claims):
    """Goes through Log in with Google as Google would answer with `claims`; returns the final redirect."""
    start = client.get("/api/auth/google", params={"next": next} if next else None, follow_redirects=False)
    assert start.status_code == 303, start.text
    query = parse_qs(urlsplit(start.headers["location"]).query)
    code = json.dumps({"email": email, "nonce": query["nonce"][0], **claims})
    return client.get("/api/auth/google/callback", params={"code": code, "state": query["state"][0]},
                      follow_redirects=False)


def register(client, email="ana@example.com", accept_privacy=True, **claims):
    """Logs in with Google (making the account the first time) and accepts the privacy notice."""
    response = google_login(client, email, **claims)
    assert response.headers["location"] == "/", response.headers["location"]
    if accept_privacy:
        assert client.post("/api/auth/privacy").status_code == 200
    return client.get("/api/auth/me")


def login_error(response) -> str:
    """The message a failed log-in shows on the log-in page."""
    location = urlsplit(response.headers["location"])
    assert location.path == "/login", location
    return parse_qs(location.query)["error"][0]


def wait_for(client, run_id, statuses=("done", "failed"), timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.2)
    raise AssertionError(f"run still {run['status']} after {timeout}s")


def upload(client, video, name="Invoice process.mp4", **data):
    with open(video, "rb") as f:
        return client.post("/api/runs", files={"file": (name, f, "video/mp4")}, data={"consent": "true", **data})


def test_google_login(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/api/auth/me").status_code == 401
        start = client.get("/api/auth/google", params={"next": "/runs/abc"}, follow_redirects=False)
        google = urlsplit(start.headers["location"])
        query = parse_qs(google.query)
        assert google.netloc == "accounts.google.com" and query["client_id"] == ["test-client"]
        assert query["redirect_uri"] == ["http://testserver/api/auth/google/callback"]
        assert "openid" in query["scope"][0] and query["state"][0] and query["nonce"][0]
        assert "httponly" in start.headers["set-cookie"].lower()

        # A new account, made on the first log-in, named after the Google account.
        done = google_login(client, "Ana@Example.com", next="/runs/abc", name=" Ana Lima ")
        assert done.headers["location"] == "/runs/abc"
        cookie = next(c for c in done.headers.get_list("set-cookie") if c.startswith("pdd_session=")).lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        me = client.get("/api/auth/me").json()
        assert me == {**me, "email": "ana@example.com", "name": "Ana Lima", "privacy_accepted": False}

        # Logging in again is the same account; the name chosen in Pidi stays.
        client.patch("/api/auth/me", json={"name": "Ana L."})
        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").status_code == 401
        google_login(client, "ana@example.com", name="Ana Lima")
        assert client.get("/api/auth/me").json() == {**me, "name": "Ana L."}

        client.cookies.set("pdd_session", "forged.token.value")
        assert client.get("/api/auth/me").status_code == 401


def test_google_login_is_checked(tmp_path):
    with make_client(tmp_path) as client:
        # Only somewhere on this site after logging in.
        for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
            assert google_login(client, next=target).headers["location"] == "/"
        # Google's answer must match the log-in this browser started.
        start = client.get("/api/auth/google", follow_redirects=False)
        query = parse_qs(urlsplit(start.headers["location"]).query)
        good = {"email": "ana@example.com", "nonce": query["nonce"][0]}
        wrong_state = client.get("/api/auth/google/callback", follow_redirects=False,
                                 params={"code": json.dumps(good), "state": "forged"})
        assert "couldn't be verified" in login_error(wrong_state)
        client.get("/api/auth/google", follow_redirects=False)  # a newer log-in replaces that one's state
        replayed = client.get("/api/auth/google/callback", follow_redirects=False,
                              params={"code": json.dumps(good), "state": query["state"][0]})
        assert "couldn't be verified" in login_error(replayed)
        client.cookies.clear()
        no_cookie = client.get("/api/auth/google/callback", params={"code": "x", "state": "y"}, follow_redirects=False)
        assert "another browser" in login_error(no_cookie)
        start = client.get("/api/auth/google", follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        cancelled = client.get("/api/auth/google/callback", params={"error": "access_denied", "state": state},
                               follow_redirects=False)
        assert login_error(cancelled) == "Log in with Google was cancelled."

        assert "isn't verified" in login_error(google_login(client, "x@example.com", email_verified=False))
        assert client.get("/api/auth/me").status_code == 401
    with make_client(tmp_path / "unset", google_client_id="") as client:
        assert "isn't set up" in login_error(client.get("/api/auth/google", follow_redirects=False))
        assert client.get("/api/site").json()["google"] is False


def test_google_accounts_and_existing_ones(tmp_path):
    with make_client(tmp_path) as client:
        # An account from before Google log-in (same email) becomes this Google account's.
        with session(client.app.state.engine) as db:
            db.add(User(email="ana@example.com", password_hash="old-hash", name="Ana"))
            db.commit()
        register(client, "ana@example.com", name="Ana Google")
        assert client.get("/api/auth/me").json()["name"] == "Ana"
        # Another Google account with that email doesn't get in.
        client.post("/api/auth/logout")
        other = google_login(client, "ana@example.com", sub="someone-else")
        assert "another Google account" in login_error(other)
        # The email follows the Google account when it changes there.
        google_login(client, "ana@new.com", sub="google-ana@example.com")
        assert client.get("/api/auth/me").json()["email"] == "ana@new.com"


def test_signup_can_be_limited(tmp_path):
    with make_client(tmp_path, signup_domains=["company.com"]) as client:
        start = client.get("/api/auth/google", follow_redirects=False)
        assert parse_qs(urlsplit(start.headers["location"]).query)["hd"] == ["company.com"]
        assert "@company.com" in login_error(google_login(client, "x@gmail.com"))
        register(client, "x@company.com")
    with make_client(tmp_path / "closed", allow_signup=False) as client:
        assert "Sign-up is closed" in login_error(google_login(client, "x@company.com"))
        with session(client.app.state.engine) as db:  # added by an administrator (web.create_user)
            db.add(User(email="x@company.com"))
            db.commit()
        register(client, "x@company.com")


def test_upload_to_document(tmp_path, video):
    with make_client(tmp_path) as client:
        assert upload(client, video).status_code == 401
        register(client)
        response = upload(client, video)
        assert response.status_code == 201, response.text
        run = wait_for(client, response.json()["id"])
        assert run["status"] == "done", run
        assert run["process_name"] == "Create invoice"
        assert run["summary"]["steps"] > 5 and run["summary"]["screenshots"] == run["summary"]["steps"] - 1
        assert any(w.startswith("Step 2 has no screenshot") for w in run["summary"]["warnings"])

        model = client.get(f"/api/runs/{run['id']}/model").json()
        assert "main_frame_path" not in model["as_is"][0]  # server paths aren't exposed
        image = client.get(model["as_is"][0]["image_url"])
        assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"

        doc = client.get(f"/api/runs/{run['id']}/document")
        assert doc.status_code == 200 and doc.content[:2] == b"PK"  # a .docx is a zip file
        assert "Create%20invoice%20-%20PDD.docx" in doc.headers["content-disposition"]

        assert [r["id"] for r in client.get("/api/runs").json()] == [run["id"]]
        assert client.delete(f"/api/runs/{run['id']}").status_code == 204
        assert client.get(f"/api/runs/{run['id']}").status_code == 404
        assert not (tmp_path / "data" / "runs" / run["id"]).exists()


def test_document_language(tmp_path, video):
    """English and Brazilian Portuguese ship with Pidi; other languages are translated by the model, once."""
    with make_client(tmp_path) as client:
        register(client)
        languages = client.get("/api/site").json()["languages"]
        assert languages[:2] == [{"code": "en", "name": "English", "translated": False},
                                 {"code": "pt-BR", "name": "Português (Brasil)", "translated": False}]
        assert {"code": "es", "name": "Español", "translated": True} in languages
        refused = upload(client, video, language="<script>")
        assert refused.status_code == 422 and "Choose a language" in refused.json()["detail"]
        headings = {}
        for language in ("en", "pt-BR", "Spanish", "Tagalog"):
            created = upload(client, video, language=language).json()
            run = wait_for(client, created["id"])
            assert run["status"] == "done", run
            doc = Document(io.BytesIO(client.get(f"/api/runs/{run['id']}/document").content))
            headings[run["language"]] = {p.text for p in doc.paragraphs}
            assert (created["stages"][0] == "translate_template") == (language in ("Spanish", "Tagalog"))
        assert set(headings) == {"en", "pt-BR", "es", "Tagalog"}  # "Spanish" is the listed "es"
        assert {"7. Process Details", "8. Exceptions"} <= headings["en"]
        assert "7. Detalhamento do Processo" not in headings["en"]
        assert {"7. Detalhamento do Processo", "8. Exceções"} <= headings["pt-BR"]
        assert any(t.startswith("S1  ") for t in headings["en"])
        assert any(t.startswith("P1  ") for t in headings["pt-BR"])
        assert "~7. Process Details" in headings["es"] and any(t.startswith("T1  ") for t in headings["es"])
        cached = client.app.state.settings.translations_dir
        assert len(list(cached.iterdir())) == 2  # Spanish and Tagalog, each translated once


def test_rejects_files_that_are_not_videos(tmp_path):
    with make_client(tmp_path) as client:
        register(client)
        consent = {"consent": "true"}
        assert client.post("/api/runs", files={"file": ("notes.txt", b"hello", "text/plain")},
                           data=consent).status_code == 415
        assert client.post("/api/runs", files={"file": ("fake.mp4", b"not a video", "video/mp4")},
                           data=consent).status_code == 415
        assert client.get("/api/runs").json() == []
        assert list((tmp_path / "data" / "runs").iterdir()) == []  # nothing left behind


def test_upload_size_limit(tmp_path):
    with make_client(tmp_path, max_upload_mb=1) as client:
        register(client)
        big = b"\0" * (1024 * 1024 + 1)
        assert client.post("/api/runs", files={"file": ("big.mp4", big, "video/mp4")},
                           data={"consent": "true"}).status_code == 413


def test_users_only_see_their_own_runs(tmp_path, video):
    with make_client(tmp_path) as ana:
        bo = TestClient(ana.app)  # same server, separate browser (cookies)
        register(ana, "ana@example.com")
        run = wait_for(ana, upload(ana, video).json()["id"])
        register(bo, "bo@example.com")
        assert bo.get("/api/runs").json() == []
        for path in ("", "/model", "/frames/F001", "/document"):
            assert bo.get(f"/api/runs/{run['id']}{path}").status_code == 404
        assert bo.delete(f"/api/runs/{run['id']}").status_code == 404
        assert ana.get(f"/api/runs/{run['id']}/frames/..%2Fapp.db").status_code == 404


def test_failed_run_can_be_retried(tmp_path, video):
    attempts = []

    def flaky(video_path, run_dir, progress, **_):
        attempts.append(run_dir)
        if len(attempts) == 1:
            raise RuntimeError("Error code: 429 - insufficient_quota")
        return fake_run(video_path, run_dir, progress)

    with make_client(tmp_path, run_fn=flaky) as client:
        register(client)
        run = wait_for(client, upload(client, video).json()["id"])
        assert run["status"] == "failed" and "ran out of credit" in run["error"]
        assert client.post(f"/api/runs/{run['id']}/retry").json()["status"] == "queued"
        assert wait_for(client, run["id"])["status"] == "done"
        assert attempts[0] == attempts[1]  # same folder, so finished stages are reused


def test_runs_interrupted_by_a_restart_continue(tmp_path, video):
    stuck = threading.Event()
    with make_client(tmp_path, run_fn=lambda *a, **k: stuck.wait()) as client:
        register(client)
        run_id = upload(client, video).json()["id"]
        engine = client.app.state.engine
    # The server "stopped" while the run was in progress.
    with session(engine) as db:
        run = db.get(Run, run_id)
        run.status = "running"
        db.add(run)
        db.commit()
    with make_client(tmp_path) as client:
        google_login(client)
        assert wait_for(client, run_id)["status"] == "done"


def test_serves_the_front_end(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("secret")
    with TestClient(create_app(Settings(data_dir=tmp_path / "data", frontend_dir=dist), run_fn=fake_run)) as client:
        assert client.get("/runs/abc").text == "<div id=root></div>"  # app routes get the page
        assert client.get("/assets/app.js").text == "console.log(1)"
        assert client.get("/api/nothing").status_code == 404
        assert "secret" not in client.get("/..%2Fsecret.txt").text
        assert client.get("/").headers["x-frame-options"] == "DENY"


def test_templates(tmp_path, video):
    template = build_template(str(tmp_path / "Company PDD.docx"), str(tmp_path / "example.png"))
    with make_client(tmp_path, run_fn=fake_template_run) as client:
        register(client)
        assert client.post("/api/templates", files={"file": ("notes.txt", b"hi", "text/plain")}).status_code == 415
        assert client.post("/api/templates", files={"file": ("fake.docx", b"not a docx", "application/zip")}).status_code == 415
        with open(template, "rb") as f:
            response = client.post("/api/templates", files={"file": ("Company PDD.docx", f)})
        assert response.status_code == 201, response.text
        detail = response.json()
        assert detail["language"] == "Brazilian Portuguese" and detail["step_label"] == "P1 - "
        removed = {b["text"] for b in detail["blocks"] if b["action"] == "remove"}
        assert "Login SAP" in removed and "P1 - Realizar abertura do SAP 760" in removed
        assert [t["name"] for t in client.get("/api/templates").json()] == ["Company PDD.docx"]

        with open(video, "rb") as f:
            response = client.post("/api/runs", files={"file": ("Invoices.mp4", f, "video/mp4")},
                                   data={"consent": "true", "template_id": detail["id"]})
        assert response.status_code == 201, response.text
        assert response.json()["stages"][0] == "analyze_template"
        run = wait_for(client, response.json()["id"])
        assert run["status"] == "done", run
        assert run["template_name"] == "Company PDD.docx" and run["language"] is None  # the template's own
        assert run["summary"]["to_complete"] >= 4
        assert any("highlighted in yellow" in w for w in run["summary"]["warnings"])

        # Deleting the template doesn't affect documents already made with it.
        assert client.delete(f"/api/templates/{detail['id']}").status_code == 204
        assert client.get(f"/api/templates/{detail['id']}").status_code == 404
        assert client.get(f"/api/runs/{run['id']}/document").status_code == 200

        # Other people can't see or use someone's template.
        with open(template, "rb") as f:
            mine = client.post("/api/templates", files={"file": ("Mine.docx", f)}).json()
        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.get("/api/templates").json() == []
        assert client.get(f"/api/templates/{mine['id']}").status_code == 404
        with open(video, "rb") as f:
            response = client.post("/api/runs", files={"file": ("x.mp4", f, "video/mp4")},
                                   data={"consent": "true", "template_id": mine["id"]})
        assert response.status_code == 404


def test_profile(tmp_path):
    with make_client(tmp_path) as client:
        register(client, name="Ana Lima")
        updated = client.patch("/api/auth/me", json={"name": " Ana L. "})
        assert updated.status_code == 200 and updated.json()["name"] == "Ana L."
        # The email is the Google account's; it can't be changed here.
        client.patch("/api/auth/me", json={"name": "Ana", "email": "other@example.com"})
        assert client.get("/api/auth/me").json()["email"] == "ana@example.com"


def test_template_cancelled_while_read_is_not_added(tmp_path, monkeypatch):
    """The page stopped waiting (Cancel) while the template was analyzed: nothing is kept."""
    from starlette.requests import Request

    async def gone(self):
        return True
    template = build_template(str(tmp_path / "Company PDD.docx"), str(tmp_path / "example.png"))
    with make_client(tmp_path) as client:
        register(client)
        monkeypatch.setattr(Request, "is_disconnected", gone)
        with open(template, "rb") as f:
            assert client.post("/api/templates", files={"file": ("Company PDD.docx", f)}).status_code == 499
        monkeypatch.undo()
        assert client.get("/api/templates").json() == []
        assert list((tmp_path / "data" / "templates").iterdir()) == []


def test_correcting_a_template(tmp_path):
    """The author changes what the analysis decided for a part of the template, and can go back to it."""
    template = build_template(str(tmp_path / "Company PDD.docx"), str(tmp_path / "example.png"))
    with make_client(tmp_path, run_fn=fake_template_run) as client:
        register(client)
        with open(template, "rb") as f:
            detail = client.post("/api/templates", files={"file": ("Company PDD.docx", f)}).json()
        tid = detail["id"]
        block = next(b for b in detail["blocks"] if b["action"] == "remove" and b["kind"] == "paragraph" and b["editable"])
        url = f"/api/templates/{tid}/blocks/{block['index']}"

        kept = client.put(url, json={"action": "keep"})
        assert kept.status_code == 200, kept.text
        changed = next(b for b in kept.json()["blocks"] if b["index"] == block["index"] and b["kind"] != "steps")
        assert changed["action"] == "keep" and changed["original"]["action"] == "remove" and changed["reason"] == ""
        assert client.put(url, json={"action": "write"}).status_code == 422  # what to write?
        assert client.put(url, json={"action": "table_rows", "purpose": "x"}).status_code == 422  # not a table
        written = client.put(url, json={"action": "write", "purpose": "  The   process owner  "}).json()
        changed = next(b for b in written["blocks"] if b["index"] == block["index"] and b["kind"] != "steps")
        assert changed["purpose"] == "The process owner" and changed["original"]["action"] == "remove"

        # Projects created now get the correction in their copy.
        analysis = json.loads((client.app.state.settings.templates_dir / tid / "template_analysis.json").read_text())
        assert next(b for b in analysis["blocks"] if b["index"] == block["index"])["action"] == "write"

        # Back to the analysis's decision, with its reason.
        undone = client.put(url, json={"action": "remove"}).json()
        changed = next(b for b in undone["blocks"] if b["index"] == block["index"] and b["kind"] != "steps")
        assert changed["original"] is None and changed["reason"] == block["reason"]

        fixed = next(b for b in detail["blocks"] if not b["editable"] and b["kind"] != "steps")
        assert client.put(f"/api/templates/{tid}/blocks/{fixed['index']}", json={"action": "keep"}).status_code == 409
        assert client.put(f"/api/templates/{tid}/blocks/999", json={"action": "keep"}).status_code == 404
        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.put(url, json={"action": "keep"}).status_code == 404


def test_reviewing_a_project(tmp_path, video):
    """The author approves the document or says what's wrong with it, step by step; only their own, when ready."""
    with make_client(tmp_path) as client:
        register(client)
        run = wait_for(client, upload(client, video).json()["id"])
        url = f"/api/runs/{run['id']}/review"
        assert client.get(url).json()["status"] == "pending"

        bad = [{"number": 999, "issue": "wording"}]
        assert client.put(f"{url}/feedback", json={"steps": bad}).status_code == 422  # no such step
        assert client.put(f"{url}/feedback", json={"steps": [{"number": 1, "issue": "missing_after"}]}).status_code == 422
        twice = [{"number": 1, "issue": "split"}, {"number": 1, "issue": "split"}]
        assert client.put(f"{url}/feedback", json={"steps": twice}).status_code == 422
        saved = client.put(f"{url}/feedback", json={"general": " The approval is missing. ", "steps": [
            {"number": 3, "issue": "name", "note": "It's  SAP   GUI"},
            {"number": 1, "issue": "missing_after", "note": "Log in first"}]})
        assert saved.status_code == 200, saved.text
        review = saved.json()
        assert review["status"] == "changes_requested" and review["feedback"]["general"] == "The approval is missing."
        assert [s["number"] for s in review["feedback"]["steps"]] == [1, 3]
        assert review["feedback"]["steps"][1]["note"] == "It's SAP GUI"

        approved = client.post(f"{url}/approve").json()
        assert approved["status"] == "approved" and approved["approved_at"] and approved["feedback"]["steps"]
        assert client.put(f"{url}/feedback", json={}).status_code == 409  # undo the approval first
        assert client.post(f"{url}/reopen").json()["status"] == "changes_requested"
        assert client.put(f"{url}/feedback", json={}).json()["status"] == "pending"  # taken back

        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.get(url).status_code == 404 and client.post(f"{url}/approve").status_code == 404


def test_security_policy_lets_the_page_read_a_chosen_video(tmp_path):
    """The new-project page reads the video's length in the browser (for the price or the cost estimate) from a
    blob: URL; the policy must allow that for media, or the browser refuses it."""
    with make_client(tmp_path) as client:
        policy = client.get("/api/health").headers["content-security-policy"]
        media = next(p for p in policy.split(";") if p.strip().startswith("media-src"))
        assert "blob:" in media and "frame-ancestors 'none'" in policy


def test_local_login(tmp_path, capsys):
    from web.auth import local_login_url
    with make_client(tmp_path) as client:  # off: the link does nothing
        assert "error=" in client.get("/api/auth/local?token=x", follow_redirects=False).headers["location"]
        assert client.get("/api/site").json()["local_login"] is False
    with make_client(tmp_path / "on", local_login=True, local_email="me@pidi.local") as client:
        url = local_login_url(client.app)
        assert url in capsys.readouterr().out  # printed at startup, where only whoever runs the server sees it
        wrong = client.get("/api/auth/local?token=guess", follow_redirects=False)
        assert "error=" in wrong.headers["location"] and client.get("/api/auth/me").status_code == 401
        assert client.get("/api/auth/local?token=", follow_redirects=False).headers["location"].startswith("/login")
        token = url.split("token=")[1]
        response = client.get(f"/api/auth/local?token={token}", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"
        me = client.get("/api/auth/me").json()
        assert me["email"] == "me@pidi.local"
        client.get(f"/api/auth/local?token={token}")  # again: the same account
        assert client.get("/api/auth/me").json()["id"] == me["id"]
