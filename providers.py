"""
The AI providers Pidi can run on, through LangChain: OpenAI, Anthropic (Claude), Google (Gemini) and Ollama (models
running on a machine of the server owner's, at PDD_OLLAMA_URL). Every stage asks the same thing of a model,
`llm.with_structured_output(Schema).invoke(messages)`, with screenshots as base64 `image_url` parts, so a provider
only has to give two chat models: one for the screenshots (many calls, one image each: a cheaper vision model is
enough) and one that writes the steps, the template sections and the assessment.

A run's provider is a dict, `ai = {"provider": ..., "api_key": ..., "model": ...}`: the person's own key (web/keys.py)
or the server's OpenAI key. `model`, when given, is used for both. The narration is read by OpenAI's Whisper with an
OpenAI key, and otherwise by Whisper running on the server (faster-whisper), so Claude, Gemini and Ollama need no
OpenAI key.
"""
import os

# The default models, each changeable by the server owner. Claude: the current Opus, for both.
DEFAULTS = {
    "openai": (os.getenv("PDD_MODEL", "gpt-4.1-mini"), os.getenv("PDD_STRUCTURE_MODEL", "gpt-4.1")),
    "anthropic": (os.getenv("PDD_ANTHROPIC_CAPTION_MODEL", "claude-opus-5-5"),
                  os.getenv("PDD_ANTHROPIC_MODEL", "claude-opus-5-5")),
    "gemini": (os.getenv("PDD_GEMINI_CAPTION_MODEL", "gemini-3.5-flash-lite"), os.getenv("PDD_GEMINI_MODEL", "gemini-3.8-flash")),
    "ollama": (os.getenv("PDD_OLLAMA_CAPTION_MODEL", "qwen2.5vl"), os.getenv("PDD_OLLAMA_MODEL", "qwen2.5vl")),
}
PROVIDERS = {
    "openai": {"label": "OpenAI", "key": True, "keys_url": "https://platform.openai.com/api-keys"},
    "anthropic": {"label": "Anthropic (Claude)", "key": True, "keys_url": "https://console.anthropic.com/settings/keys"},
    "gemini": {"label": "Google (Gemini)", "key": True, "keys_url": "https://aistudio.google.com/apikey"},
    "ollama": {"label": "Ollama (this server's)", "key": False, "keys_url": "https://ollama.com/library"},
}
# Ollama's default context (a few thousand tokens) is far too small for a recording's observations.
OLLAMA_CONTEXT = int(os.getenv("PDD_OLLAMA_CONTEXT", "32768"))
# Models that "think" before answering (e.g. qwen3.5) do so very slowly on a home GPU: one redo took over 20 minutes
# with thinking on. Off by default; PDD_OLLAMA_THINK=true turns it back on.
OLLAMA_THINK = os.getenv("PDD_OLLAMA_THINK", "false").strip().lower() in ("1", "true", "yes")
# The most a model writes in one answer: the step list of a long recording is the largest.
MAX_OUTPUT_TOKENS = 16000


def ollama_url() -> str:
    return os.getenv("PDD_OLLAMA_URL", "").strip().rstrip("/")


def available() -> list[str]:
    """The providers people can connect on this server: Ollama only when the server owner set PDD_OLLAMA_URL."""
    return [p for p in PROVIDERS if p != "ollama" or ollama_url()]


def models_for(ai: dict) -> tuple[str, str]:
    """(screenshots model, writing model) for a provider config."""
    caption, structure = DEFAULTS[ai.get("provider") or "openai"]
    model = (ai.get("model") or "").strip()
    return (model, model) if model else (caption, structure)


def chat_model(ai: dict, model: str, deterministic: bool = False):
    """A LangChain chat model for `ai`. `deterministic`: temperature 0 where the provider takes one, so two runs
    over the same captions give (nearly) the same steps."""
    provider, key = ai.get("provider") or "openai", ai.get("api_key") or None
    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model, max_retries=5, api_key=key, **({"temperature": 0} if deterministic else {}))
    if provider == "anthropic":
        return _claude(model, key)
    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model, google_api_key=key, max_retries=5,
                                      max_output_tokens=MAX_OUTPUT_TOKENS, **({"temperature": 0} if deterministic else {}))
    if provider == "ollama":
        if not ollama_url():
            raise ValueError("Ollama isn't set up on this server (PDD_OLLAMA_URL).")
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model, base_url=ollama_url(), num_ctx=OLLAMA_CONTEXT, reasoning=OLLAMA_THINK,
                          **({"temperature": 0} if deterministic else {}))
    raise ValueError(f"Unknown AI provider: {provider}")


def _claude(model: str, key: str | None):
    """Claude through LangChain's connector (which uses Anthropic's own SDK). Structured answers use Claude's
    structured outputs rather than a forced tool call, which current models refuse; no temperature, which
    they refuse too."""
    from langchain_anthropic import ChatAnthropic

    class Claude(ChatAnthropic):
        def with_structured_output(self, schema, **kwargs):
            kwargs.setdefault("method", "json_schema")
            return super().with_structured_output(schema, **kwargs)

    return Claude(model=model, api_key=key, max_tokens=MAX_OUTPUT_TOKENS, max_retries=5)


def chat_models(ai: dict) -> tuple:
    """(screenshots model, writing model) for a run."""
    caption, structure = models_for(ai)
    return chat_model(ai, caption), chat_model(ai, structure, deterministic=True)


def writing_model(ai: dict | None):
    """The model that writes (template reading, assessment, redo): the provider's, or the server's OpenAI one."""
    ai = ai or {"provider": "openai"}
    return chat_model(ai, models_for(ai)[1], deterministic=True)


def transcriber(ai: dict):
    """How the narration is read: OpenAI's Whisper with an OpenAI key, Whisper on the server otherwise."""
    import functools
    from transcribe import transcribe_audio, transcribe_local
    if (ai.get("provider") or "openai") == "openai":
        if ai.get("api_key"):
            from openai import OpenAI
            return functools.partial(transcribe_audio, client=OpenAI(api_key=ai["api_key"]))
        return transcribe_audio
    return transcribe_local


def check_key(provider: str, key: str) -> None:
    """Raises ValueError if the provider refuses the key. Listing models costs nothing."""
    import httpx
    try:
        if provider == "openai":
            import openai
            try:
                openai.OpenAI(api_key=key, timeout=15, max_retries=1).models.list()
            except openai.AuthenticationError:
                raise ValueError("OpenAI refused this key. Check that it was copied whole and is still active.")
            except openai.APIError as exc:
                raise ValueError(f"The key couldn't be checked with OpenAI ({type(exc).__name__}). Try again.")
        elif provider == "anthropic":
            import anthropic
            try:
                anthropic.Anthropic(api_key=key, timeout=15, max_retries=1).models.list(limit=1)
            except anthropic.AuthenticationError:
                raise ValueError("Anthropic refused this key. Check that it was copied whole and is still active.")
            except anthropic.APIError as exc:
                raise ValueError(f"The key couldn't be checked with Anthropic ({type(exc).__name__}). Try again.")
        elif provider == "gemini":
            response = httpx.get("https://generativelanguage.googleapis.com/v1beta/models",
                                 headers={"x-goog-api-key": key}, params={"pageSize": 1}, timeout=15)
            if response.status_code in (400, 401, 403):
                raise ValueError("Google refused this key. Check that it was copied whole and is still active.")
            response.raise_for_status()
        elif provider == "ollama":
            response = httpx.get(f"{ollama_url()}/api/tags", timeout=10)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ValueError(f"The {PROVIDERS[provider]['label']} connection couldn't be checked "
                         f"({type(exc).__name__}). Try again.")
