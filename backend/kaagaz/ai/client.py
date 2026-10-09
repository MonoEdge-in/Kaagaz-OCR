"""The model client's interface, and the one place that builds the client.

Everything else in the backend depends on ``ModelClient`` only, so tests use
fakes and the product runs with no AI at all (``MODEL_ENABLED=false`` gives
``build_model_client(...) is None``, and callers fall back: search without
meaning vectors, answers that quote the evidence). With MODEL_ENABLED the client is
``kaagaz.ai.openrouter.OpenRouterClient`` (PRD 5 G9, M1, M5): MODEL_CHAT answers and
MODEL_EMBED embeds, both google/gemini- models (the settings refuse any other).

Prompts are the callers' job (they fence document text as data). The client never
logs a prompt, a reply or an image, and its errors carry short codes only.

Two errors, because callers treat them differently:

* ``ModelUnavailable``: no usable HTTP reply (down, refused, timeout, a non-2xx
  status, a body that is not a JSON object, or a reply without the expected
  envelope). Safe to retry later; callers carry on without the model.
* ``ModelOutputError``: the model replied, but its content is unusable: chat
  content that is not a JSON object matching the schema even after one repair
  retry, a reply cut at its token budget, or embeddings of the wrong count,
  dimension or values.

**Budgets** (PRD 2 ML amendment 5, PRD 5 M1). ``estimate_tokens``
(``estimate_tokens_cautiously`` for the answer's evidence) and ``input_token_budget``
let callers cut their input to fit the context window, MODEL_CONTEXT_TOKENS, before
calling. The worker's chat calls (the card and the invoice windows) may use their own
timeout, ``MODEL_EXTRACT_TIMEOUT_SECONDS``.

Cost: ``estimate_tokens`` is O(n) in the text.
"""

import unicodedata
from typing import Any, Protocol

from kaagaz.ai.openrouter import OpenRouterClient, process_gate
from kaagaz.ai.replies import ModelOutputError as ModelOutputError
from kaagaz.ai.replies import ModelUnavailable as ModelUnavailable
from kaagaz.ai.replies import schema_problem as schema_problem
from kaagaz.settings import CHAT_OVERHEAD_TOKENS, Settings


class ModelClient(Protocol):
    chat_model: str
    embed_model: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per text, in order."""
        ...

    def chat_json(
        self, *, system: str, user: str, schema: dict[str, Any], max_tokens: int | None = None
    ) -> dict[str, Any]:
        """One JSON object that follows ``schema``, in at most ``max_tokens`` reply tokens."""
        ...


def estimate_tokens(text: str) -> int:
    """About how many tokens ``text`` is (ML amendment 5): an ASCII character is a
    quarter of a token, any other (Devanagari) two thirds of one. Rounded up. O(n)."""
    other = sum(1 for ch in text if ch >= "\x80")
    return -(-(3 * (len(text) - other) + 8 * other) // 12)  # ascii / 4 + other / 1.5, rounded up


def estimate_tokens_cautiously(text: str) -> int:
    """``estimate_tokens``, but a letter or mark that is not ASCII (Unicode categories L
    and M: the letters and vowel signs of Devanagari, Gujarati, Tamil, Arabic... alike) is
    one whole token. Never below ``estimate_tokens``. Rounded up. O(n).
    PROVISIONAL (council 4 Oct 2026, PR C), for the answer's evidence: a small model's
    tokenizer splits the letters of most scripts into more than two thirds of a token
    each, so evidence counted the first way could fill the window; counting more only
    leaves out more low-ranked evidence (``draft.fit_evidence``). The card and invoice
    windows keep ``estimate_tokens``."""
    if text.isascii():
        return estimate_tokens(text)
    twelfths = sum(3 if ch < "\x80" else 12 if unicodedata.category(ch)[0] in "LM" else 8 for ch in text)
    return -(-twelfths // 12)


def input_token_budget(*, num_ctx: int, max_tokens: int, system: str) -> int:
    """How many tokens the user message may take so the call fits ``num_ctx``: the room
    left after the system prompt, the reply, and a repair retry (which sends the first
    reply back and gets a second one), less ``CHAT_OVERHEAD_TOKENS``. Never below 0."""
    return max(0, num_ctx - 2 * max_tokens - estimate_tokens(system) - CHAT_OVERHEAD_TOKENS)


def build_model_client(
    settings: Settings, *, chat_timeout_seconds: float | None = None
) -> ModelClient | None:
    """The configured model client, or None when the product runs without AI.
    ``chat_timeout_seconds`` is for ``chat_json`` only (the worker passes
    MODEL_EXTRACT_TIMEOUT_SECONDS). Every client shares the process's gate
    (``openrouter.process_gate``): one limiter and one circuit breaker for the process."""
    if not settings.model_enabled:
        return None
    return OpenRouterClient(settings, chat_timeout_seconds=chat_timeout_seconds, gate=process_gate(settings))
