"""
Token cost of a run, from the usage LangChain records per model: an estimate, list prices times tokens; the
provider's own billing is the real figure.

Prices are USD per 1M tokens (standard tier): OpenAI from https://openai.com/api/pricing, Anthropic from
https://www.anthropic.com/pricing, Google from https://ai.google.dev/gemini-api/docs/pricing. Update them here
when prices change or a new model is used; models missing from the table are reported, not guessed. Models on
the server's Ollama cost nothing (`free`), and nor does Whisper running on the server.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.tracers.context import register_configure_hook

PRICES = {
    #               input, cached input, output
    "gpt-4.1":      (2.00, 0.50, 8.00),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-4o":       (2.50, 1.25, 10.00),
    "gpt-4o-mini":  (0.15, 0.075, 0.60),
    "gpt-5":        (1.25, 0.125, 10.00),
    "gpt-5-mini":   (0.25, 0.025, 2.00),
    "gpt-5-nano":   (0.05, 0.005, 0.40),
    # Anthropic (Claude); cached input = cache reads
    "claude-fable-5-2":  (8.00, 0.20, 40.00),
    "claude-fable-5-1":  (10.00, 1.00, 50.00),
    "claude-opus-5-5":   (4.00, 0.20, 20.00),
    "claude-opus-5":     (5.00, 0.50, 25.00),
    "claude-opus-4-8":   (5.00, 0.50, 25.00),
    "claude-opus-4-7":   (5.00, 0.50, 25.00),
    "claude-opus-4-6":   (5.00, 0.50, 25.00),
    "claude-sonnet-5-5": (2.00, 0.10, 10.00),
    "claude-sonnet-5":   (2.00, 0.20, 10.00),
    "claude-sonnet-4-6": (3.00, 0.30, 15.00),
    "claude-haiku-5-5":  (0.10, 0.01, 0.50),
    "claude-haiku-4-5":  (1.00, 0.10, 5.00),
    # Google (Gemini), standard tier, text and image input (checked 2026-11-20). Where Google has announced a
    # price change (Gemini 4 Carbon and Argon on 2026-12-01, 3.8 and 3.6 Flash on 2027-01-01), the new price.
    "gemini-4-carbon":       (4.00, 0.20, 20.00),
    "gemini-4-argon":        (4.00, 0.20, 20.00),
    "gemini-4-neon":         (3.00, 0.15, 15.00),
    "gemini-4-hydrogen":     (1.50, 0.075, 7.50),
    "gemini-3.8-flash":      (1.50, 0.15, 7.50),
    "gemini-3.6-flash":      (1.50, 0.15, 7.50),
    "gemini-3.5-flash-lite": (0.30, 0.03, 2.50),
    # Shut down 2026-12-01 (requests then go to 3.8 Flash and 3.5 Flash-Lite); kept for older projects' costs.
    "gemini-2.5-pro":        (1.25, 0.125, 10.00),
    "gemini-2.5-flash":      (0.30, 0.03, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.01, 0.40),
}
WHISPER_PER_MINUTE = 0.006


def price_for(model: str) -> tuple[float, float, float] | None:
    """Responses name dated snapshots (e.g. "gpt-4.1-mini-2025-04-14"), so the longest matching prefix wins."""
    matches = [name for name in PRICES if model == name or model.startswith(name + "-")]
    return PRICES[max(matches, key=len)] if matches else None


def cost_by_model(usage_by_model: dict[str, dict], free: bool = False) -> tuple[dict[str, float], list[str]]:
    """
    `usage_by_model` is UsageMetadataCallbackHandler.usage_metadata: {model: {"input_tokens",
    "output_tokens", "input_token_details": {"cache_read"}}}. Returns the cost of each priced model and the
    models with no price. `free`: the models ran on the server (Ollama), so they cost nothing.
    """
    costs, unknown = {}, []
    for model, usage in usage_by_model.items():
        if free:
            costs[model] = 0.0
            continue
        price = price_for(model)
        if price is None:
            unknown.append(model)
            continue
        cached = (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
        fresh = usage.get("input_tokens", 0) - cached
        costs[model] = (fresh * price[0] + cached * price[1] + usage.get("output_tokens", 0) * price[2]) / 1_000_000
    return costs, unknown


def usage_cost(usage_by_model: dict[str, dict], free: bool = False) -> tuple[float, list[str]]:
    """The total of cost_by_model, and the models with no price."""
    costs, unknown = cost_by_model(usage_by_model, free)
    return sum(costs.values()), unknown


@contextmanager
def measured():
    """Collects the token usage of the model calls made inside it, for recording what they cost (web/usage.py)."""
    with usage_with_limit(None) as usage:
        yield usage


class CostLimitReached(Exception):
    """A run's model calls cost more than it was allowed to (see usage_with_limit)."""


class _LimitedUsage(UsageMetadataCallbackHandler):
    raise_error = True  # LangChain otherwise logs a handler's exception and goes on

    def __init__(self, limit: float | None, spent: float):
        super().__init__()
        self.limit, self.spent = limit, spent

    def on_llm_end(self, response, **kwargs) -> None:
        super().on_llm_end(response, **kwargs)
        if self.limit is not None:
            cost = usage_cost(self.usage_metadata)[0] + self.spent
            if cost > self.limit:
                raise CostLimitReached(f"The run cost ${cost:.2f}, over its limit of ${self.limit:.2f}.")


_usage_var: ContextVar[_LimitedUsage | None] = ContextVar("pdd_usage", default=None)
register_configure_hook(_usage_var, inheritable=True)


@contextmanager
def usage_with_limit(limit: float | None = None, spent: float = 0.0):
    """
    Like LangChain's get_usage_metadata_callback: collects the token usage of every model call made inside
    it (also in threads that copy the context). With `limit`, the call that takes the cost (plus `spent`,
    e.g. by earlier attempts) over it raises CostLimitReached, so a run that turns out far more expensive
    than expected stops instead of going on.
    """
    handler = _LimitedUsage(limit, spent)
    token = _usage_var.set(handler)
    try:
        yield handler
    finally:
        _usage_var.reset(token)
