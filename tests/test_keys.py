"""Billing "keys": people connect their own OpenAI key and each project runs on it, with no credits."""
import json

from tests.test_web import fake_run, make_client, register, upload, video, wait_for  # noqa: F401
from web.db import ApiKey, session

GOOD = "sk-test-1234567890abcdefWXYZ"


def test_projects_run_on_peoples_own_keys(tmp_path, video):
    used = []

    def run_fn(*args, ai=None, **kwargs):
        used.append(ai)
        return fake_run(*args, **kwargs)

    with make_client(tmp_path, run_fn=run_fn, billing="keys") as client:
        register(client)
        site = client.get("/api/site").json()
        assert site["billing"] == "keys" and site["server_key"] is False
        assert client.get("/api/credits").json()["balance"] == 0  # no welcome credits

        refused = upload(client, video)
        assert refused.status_code == 400 and "Account page" in refused.json()["detail"]

        assert client.post("/api/keys", json={"key": "short"}).status_code == 422
        bad = client.post("/api/keys", json={"key": "sk-bad-1234567890abcdefghij"})
        assert bad.status_code == 422 and "refused" in bad.json()["detail"]
        added = client.post("/api/keys", json={"name": " Work ", "key": f"  {GOOD} "})
        assert added.status_code == 201, added.text
        key = added.json()
        assert key["name"] == "Work" and key["hint"] == "sk-…WXYZ"
        assert client.get("/api/keys").json()["keys"] == [key]
        with session(client.app.state.engine) as db:
            assert GOOD not in db.get(ApiKey, key["id"]).secret  # stored encrypted

        run = upload(client, video)
        assert run.status_code == 201, run.text
        run = wait_for(client, run.json()["id"])
        assert run["status"] == "done" and run["credits"] is None
        assert used == [{"provider": "openai", "api_key": GOOD, "model": None}]
        assert client.get("/api/credits").json()["balance"] == 0  # nothing charged

        # Nobody else can see, use or remove someone's key.
        export = client.get("/api/account/export").json()
        assert export["api_keys"][0]["hint"] == "sk-…WXYZ" and GOOD not in json.dumps(export)
        client.post("/api/auth/logout")
        register(client, "bo@example.com")
        assert client.get("/api/keys").json()["keys"] == []
        assert client.delete(f"/api/keys/{key['id']}").status_code == 404
        assert upload(client, video, api_key_id=key["id"]).status_code == 404


def test_a_removed_key_stops_its_projects(tmp_path, video):
    import threading
    gate = threading.Event()

    def slow(*args, **kwargs):
        gate.wait(10)
        return fake_run(*args, **kwargs)

    with make_client(tmp_path, run_fn=slow, billing="keys", workers=1) as client:
        register(client)
        first = client.post("/api/keys", json={"key": GOOD}).json()
        second = client.post("/api/keys", json={"key": GOOD.replace("WXYZ", "ABCD")}).json()
        running = upload(client, video, api_key_id=first["id"]).json()  # keeps the worker busy
        queued = upload(client, video, api_key_id=second["id"]).json()
        assert client.delete(f"/api/keys/{second['id']}").status_code == 204
        gate.set()
        assert wait_for(client, running["id"])["status"] == "done"
        failed = wait_for(client, queued["id"])
        assert failed["status"] == "failed" and "was removed" in failed["error"]
        retry = client.post(f"/api/runs/{queued['id']}/retry")
        assert retry.status_code == 409 and "was removed" in retry.json()["detail"]


def test_the_server_key_when_allowed(tmp_path, video, monkeypatch):
    used = []
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server")

    def run_fn(*args, ai=None, **kwargs):
        used.append(ai)
        return fake_run(*args, **kwargs)

    with make_client(tmp_path, run_fn=run_fn, billing="keys", allow_server_key=True) as client:
        register(client)
        assert client.get("/api/keys").json()["server_key"] is True
        run = wait_for(client, upload(client, video).json()["id"])
        assert run["status"] == "done" and used == [None]  # None: the server's key


def test_keys_are_off_with_credits(tmp_path):
    with make_client(tmp_path) as client:
        register(client)
        assert client.get("/api/site").json()["billing"] == "credits"
        assert client.get("/api/keys").status_code == 404
        assert client.post("/api/keys", json={"key": GOOD}).status_code == 404


def test_other_providers(tmp_path, video, monkeypatch):
    """Claude, Gemini and the server's Ollama: each project runs on the provider of the key it was made with,
    with the model the person chose, if any."""
    used = []

    def run_fn(*args, ai=None, **kwargs):
        used.append(ai)
        return fake_run(*args, **kwargs)

    monkeypatch.delenv("PDD_OLLAMA_URL", raising=False)
    with make_client(tmp_path, run_fn=run_fn, billing="keys") as client:
        register(client)
        offered = [p["id"] for p in client.get("/api/keys").json()["providers"]]
        assert offered == ["openai", "anthropic", "gemini"]  # Ollama only when the server owner set it up
        assert client.post("/api/keys", json={"provider": "ollama"}).status_code == 422
        assert client.post("/api/keys", json={"provider": "mistral", "key": GOOD}).status_code == 422
        assert client.post("/api/keys", json={"provider": "anthropic", "key": GOOD, "model": "two words"}).status_code == 422
        claude = client.post("/api/keys", json={"provider": "anthropic", "key": "sk-ant-test-1234567890abcdWXYZ",
                                                "model": " claude-sonnet-5-5 "}).json()
        assert claude["name"] == "Anthropic (Claude)" and claude["model"] == "claude-sonnet-5-5"
        gemini = client.post("/api/keys", json={"provider": "gemini", "key": "AIza-test-1234567890abcdefWXYZ"}).json()
        wait_for(client, upload(client, video, api_key_id=claude["id"]).json()["id"])
        wait_for(client, upload(client, video, api_key_id=gemini["id"]).json()["id"])
        assert used == [{"provider": "anthropic", "api_key": "sk-ant-test-1234567890abcdWXYZ", "model": "claude-sonnet-5-5"},
                        {"provider": "gemini", "api_key": "AIza-test-1234567890abcdefWXYZ", "model": None}]

        monkeypatch.setenv("PDD_OLLAMA_URL", "http://ollama.internal:11434")
        assert "ollama" in [p["id"] for p in client.get("/api/keys").json()["providers"]]
        local = client.post("/api/keys", json={"provider": "ollama", "model": "llama3.2-vision"})
        assert local.status_code == 201 and local.json()["hint"] == ""
        wait_for(client, upload(client, video, api_key_id=local.json()["id"]).json()["id"])
        assert used[-1] == {"provider": "ollama", "api_key": None, "model": "llama3.2-vision"}


def test_provider_models():
    """Each provider's models: its defaults, or the one the person chose for both."""
    import providers
    assert providers.models_for({"provider": "anthropic"}) == ("claude-opus-5-5", "claude-opus-5-5")
    assert providers.models_for({"provider": "openai", "model": "gpt-5"}) == ("gpt-5", "gpt-5")
    claude = providers.chat_model({"provider": "anthropic", "api_key": "sk-ant-x"}, "claude-opus-5-5", deterministic=True)
    assert claude.model == "claude-opus-5-5" and claude.temperature is None  # current Claude models refuse it
    from pydantic import BaseModel

    class Answer(BaseModel):
        text: str
    bound = claude.with_structured_output(Answer)  # Claude's structured outputs, not a forced tool call
    assert "output_config" in str(bound.first.kwargs if hasattr(bound, "first") else bound)
