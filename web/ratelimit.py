"""
Slows down repeated failed log-ins: a key (an IP address) may have at most `limit` events in `window`
seconds; after that the request gets 429 with how long to wait.

Counted in memory, which fits this app: it runs as one process (the job queue lives in it too). The client
address is request.client.host, which uvicorn takes from X-Forwarded-For when it runs behind the proxy
with --proxy-headers (see the Dockerfile).
"""
import math
import threading
import time
from collections import deque
from fastapi import HTTPException, Request, status


class Limiter:
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._events: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque:
        """The key's events within the window; keys with none left are forgotten, so memory stays bounded."""
        events = self._events.get(key, deque())
        while events and events[0] <= now - self.window:
            events.popleft()
        if not events:
            self._events.pop(key, None)
        return events

    def check(self, *keys: str) -> None:
        """Raises 429 if any of the keys has reached its limit."""
        now = time.monotonic()
        with self._lock:
            for key in keys:
                events = self._recent(key, now)
                if len(events) >= self.limit:
                    wait = math.ceil(events[0] + self.window - now)
                    minutes = max(1, math.ceil(wait / 60))
                    when = f"{minutes} minute{'s' if minutes > 1 else ''}"
                    raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"Too many attempts. Try again in {when}.",
                                        headers={"Retry-After": str(wait)})

    def hit(self, *keys: str) -> None:
        now = time.monotonic()
        with self._lock:
            for key in keys:
                events = self._recent(key, now)
                events.append(now)
                self._events[key] = events

    def take(self, *keys: str) -> None:
        """check then hit: for actions limited however they turn out (e.g. sending an email)."""
        self.check(*keys)
        self.hit(*keys)

    def clear(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._events.pop(key, None)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class Limits:
    """The app's limits, one set per app (so tests start from zero)."""

    def __init__(self):
        # Failed Google log-ins per address (forged or replayed answers, refused domains).
        self.signin_ip = Limiter(30, 15 * 60)
        # Template uploads per person: each is analyzed on the server's OpenAI key, free of credits.
        self.template_user = Limiter(20, 24 * 3600)
        # Checkouts started per person (each one is made at Asaas).
        self.purchase_user = Limiter(10, 3600)
        # Saves of a project's ROI per person: each one writes the document again.
        self.roi_user = Limiter(120, 3600)
        # Keys connected per person (each one is checked with OpenAI).
        self.key_user = Limiter(20, 3600)
        # Redos per person (each one is a model call).
        self.redo_user = Limiter(30, 3600)
