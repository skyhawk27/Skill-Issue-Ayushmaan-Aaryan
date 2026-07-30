"""LLM provider configuration, request pacing, and retry.

Why this exists
---------------
Member 3's retrieval and chat construct a bare ``OpenAI()``, which points at
api.openai.com and asks for ``gpt-4o`` / ``text-embedding-3-small``. Those are
paid. Every free OpenAI-compatible provider serves different model names, so
switching needs three things changed — base URL, key, model names — none of which
were configurable.

Rather than edit a teammate's files, this configures the provider from the
environment and injects it through the ``client=`` parameter both of their public
functions already accept. Setting nothing keeps the previous OpenAI behaviour
exactly.

The rate limit is the real problem
----------------------------------
Free tiers are request-capped (NVIDIA NIM: 40/min). Measured against this app:

* ``build_index`` — 2 requests (75 chunks at batch size 64), once per document.
* each question — 2 requests (one query embedding, one completion).
* **the summarizer — 16 at once**, because it fans out one call per page with
  ``asyncio.gather`` and then synthesises.

So chat is not the glutton; the summarizer's burst exhausts the minute's budget
at upload time and chat gets 429'd immediately afterwards. Pacing only the chat
path would have fixed the wrong thing.

Two mechanisms, deliberately both:

1. **A process-wide rate limiter.** One shared window across *every* caller, so
   the summarizer's burst and the chat path draw from the same budget instead of
   each politely staying under the cap alone and together going over it.
2. **SDK-level retry.** The OpenAI SDK honours ``Retry-After`` on 429 when
   ``max_retries`` allows it, which absorbs whatever the limiter did not smooth.
"""

from __future__ import annotations

import collections
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger("paperlens.llm")

#: Requests per rolling minute, across the whole process. Default sits under
#: NVIDIA NIM's 40/min so a burst has headroom rather than landing exactly on the
#: cap. Override with PAPERLENS_RATE_LIMIT_PER_MIN.
_DEFAULT_RATE_LIMIT = 28

#: How many times the SDK retries a 429/5xx before giving up. Higher than the
#: default 2 because a free tier's whole failure mode is transient throttling.
_DEFAULT_MAX_RETRIES = 6

_DEFAULT_TIMEOUT_S = 60.0


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def base_url() -> str:
    return _env("PAPERLENS_LLM_BASE_URL")


def api_key() -> str:
    """The provider key, falling back through the plausible variables."""
    return (
        _env("PAPERLENS_LLM_API_KEY")
        or _env("NVIDIA_API_KEY")
        or _env("OPENAI_API_KEY")
    )


def chat_model() -> str:
    return _env("PAPERLENS_CHAT_MODEL")


def embedding_model() -> str:
    return _env("PAPERLENS_EMBEDDING_MODEL")


def is_configured() -> bool:
    """True when a provider override is present and usable."""
    return bool(base_url() and api_key())


# ─── Rate limiting ─────────────────────────────────────────────────────────


class _RateLimiter:
    """A rolling-window limiter shared by every caller in the process.

    Blocking rather than failing: the caller would only have to sleep and retry
    anyway, and doing it here keeps the waiting invisible to the panels.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._max = max(1, max_per_minute)
        self._calls: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 60.0:
                    self._calls.popleft()
                if len(self._calls) < self._max:
                    self._calls.append(now)
                    return
                wait = 60.0 - (now - self._calls[0])
            logger.info("paperlens: pacing LLM request, waiting %.1fs", wait)
            # Capped so a long wait stays interruptible and re-checks the window.
            time.sleep(min(max(wait, 0.05), 5.0))


_limiter = _RateLimiter(
    int(_env("PAPERLENS_RATE_LIMIT_PER_MIN") or _DEFAULT_RATE_LIMIT)
)


def limiter() -> _RateLimiter:
    return _limiter


# ─── Throttled client proxy ────────────────────────────────────────────────
# The teammate functions accept `client=`, so anything exposing the same surface
# can be injected. These proxies pace the two endpoints actually used and
# delegate everything else untouched.


#: Model-name fragments that mark an *asymmetric* embedding model — one that
#: embeds a question and a passage differently and therefore requires NVIDIA's
#: ``input_type``. Without it the call fails outright:
#: ``400 'input_type' parameter is required for asymmetric models``.
_ASYMMETRIC_HINTS = ("embedqa", "-qa-", "asym")


def _needs_input_type(model: str) -> bool:
    lowered = model.lower()
    return any(hint in lowered for hint in _ASYMMETRIC_HINTS)


class _ThrottledEmbeddings:
    def __init__(self, inner: Any, model: str) -> None:
        self._inner = inner
        self._model = model
        self._asymmetric = _needs_input_type(model)

    def create(self, *args: Any, **kwargs: Any) -> Any:
        if self._model:
            # The caller hardcodes an OpenAI model name; the provider serves a
            # different catalogue. Overriding here avoids editing their module.
            kwargs["model"] = self._model

        if self._asymmetric:
            extra = dict(kwargs.get("extra_body") or {})
            extra.setdefault("input_type", self._input_type(kwargs.get("input")))
            extra.setdefault("truncate", "END")
            kwargs["extra_body"] = extra

        _limiter.acquire()
        return self._inner.create(*args, **kwargs)

    @staticmethod
    def _input_type(payload: Any) -> str:
        """Whether this call is embedding a question or the document.

        Index building submits batches of chunks; query embedding submits exactly
        one string. That difference is reliable here and lets an asymmetric model
        be used the way it was designed, which is the reason to prefer one.
        """
        if isinstance(payload, (list, tuple)) and len(payload) == 1:
            return "query"
        if isinstance(payload, str):
            return "query"
        return "passage"

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


class _ThrottledCompletions:
    def __init__(self, inner: Any, model: str) -> None:
        self._inner = inner
        self._model = model

    def create(self, *args: Any, **kwargs: Any) -> Any:
        if self._model:
            kwargs["model"] = self._model
        _limiter.acquire()
        return self._inner.create(*args, **kwargs)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


class _ThrottledChat:
    def __init__(self, inner: Any, model: str) -> None:
        self._inner = inner
        self.completions = _ThrottledCompletions(inner.completions, model)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


class ThrottledClient:
    """An OpenAI-compatible client that paces itself and pins provider models."""

    def __init__(self, inner: Any, *, chat_model_name: str = "",
                 embedding_model_name: str = "") -> None:
        self._inner = inner
        self.embeddings = _ThrottledEmbeddings(inner.embeddings, embedding_model_name)
        self.chat = _ThrottledChat(inner.chat, chat_model_name)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


_client_lock = threading.Lock()
_client: ThrottledClient | None = None


def client() -> Any | None:
    """The shared configured client, or ``None`` to leave callers as they were.

    Returning ``None`` matters: every teammate function treats a ``None`` client
    as "build your own default", so an unconfigured environment behaves exactly
    as it did before this module existed.
    """
    global _client
    if not is_configured():
        return None

    with _client_lock:
        if _client is not None:
            return _client
        try:
            from openai import OpenAI
        except ImportError:
            return None

        inner = OpenAI(
            base_url=base_url(),
            api_key=api_key(),
            max_retries=int(_env("PAPERLENS_MAX_RETRIES") or _DEFAULT_MAX_RETRIES),
            timeout=_DEFAULT_TIMEOUT_S,
        )
        _client = ThrottledClient(
            inner,
            chat_model_name=chat_model(),
            embedding_model_name=embedding_model(),
        )
        logger.info("paperlens: LLM provider %s (chat=%s, embed=%s)",
                    base_url(), chat_model() or "default", embedding_model() or "default")
        return _client


def pace_summarizer() -> bool:
    """Bring the summarizer's 16-call burst under the same budget.

    It builds its own module-level ``AsyncOpenAI`` at import, so it cannot be
    reached through a ``client=`` parameter like the others. Replacing that
    global with a retry-configured client is the only injection point available,
    and without it the burst drains the minute's allowance before chat gets a
    look in.

    Deliberately narrow: it only raises ``max_retries`` on an equivalent client,
    changing no behaviour beyond how patiently a 429 is handled. Returns whether
    the patch was applied.
    """
    if not is_configured():
        return False
    try:
        import summarization.briefing as briefing
        from openai import AsyncOpenAI
    except Exception:
        return False

    if getattr(briefing, "_paperlens_paced", False):
        return True

    try:
        briefing.client = AsyncOpenAI(
            base_url=base_url(),
            api_key=api_key(),
            max_retries=int(_env("PAPERLENS_MAX_RETRIES") or _DEFAULT_MAX_RETRIES),
            timeout=_DEFAULT_TIMEOUT_S,
        )
        briefing._paperlens_paced = True
        logger.info("paperlens: summarizer client re-paced with retries")
        return True
    except Exception:
        logger.exception("paperlens: could not re-pace the summarizer client")
        return False
