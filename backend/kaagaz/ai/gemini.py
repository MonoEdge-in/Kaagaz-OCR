"""The old name of the model client (PRD 5 G9): a re-export of ``kaagaz.ai.openrouter``.

Every model PRD 5 names now goes through OpenRouter (council 8 Oct 2026), so the client
lives in ``kaagaz.ai.openrouter``. This module keeps the names the reader's other tracks
code against working unchanged: ``GeminiClient`` is ``OpenRouterClient`` and
``GeminiGate`` is ``ModelGate``. It is removed in pull request 4, once every caller imports
``kaagaz.ai.openrouter``.

Migration note (history only): the client used to call Google's generativelanguage API
with an x-goog-api-key header and read GEMINI_API_KEY, GEMINI_BASE_URL and GEMINI_RPM;
those are now OPENROUTER_API_KEY, OPENROUTER_BASE_URL and MODEL_RPM.
"""

from kaagaz.ai.openrouter import (
    BACKOFF_BASE_SECONDS,
    BACKOFF_MAX_SECONDS,
    BLOCKED_FINISH_REASONS,
    BREAKER_FAILURES,
    BREAKER_OPEN_SECONDS,
    CHAT_THINKING_LEVEL,
    CHAT_THINKING_TOKENS,
    DOWN,
    FAILING,
    KEY_LATCH_SECONDS,
    KEY_PROBLEM,
    PAGE_MAX_OUTPUT_TOKENS,
    PROVIDER,
    STRICT_SCHEMA,
    WORKING,
    ModelGate,
    OpenRouterClient,
    PageReply,
    Probe,
    Usage,
    process_gate,
)
from kaagaz.settings import MEDIA_RESOLUTION_SHAPE

GeminiClient = OpenRouterClient
GeminiGate = ModelGate

__all__ = [
    "BACKOFF_BASE_SECONDS",
    "BACKOFF_MAX_SECONDS",
    "BLOCKED_FINISH_REASONS",
    "BREAKER_FAILURES",
    "BREAKER_OPEN_SECONDS",
    "CHAT_THINKING_LEVEL",
    "CHAT_THINKING_TOKENS",
    "DOWN",
    "FAILING",
    "KEY_LATCH_SECONDS",
    "KEY_PROBLEM",
    "MEDIA_RESOLUTION_SHAPE",
    "PAGE_MAX_OUTPUT_TOKENS",
    "PROVIDER",
    "STRICT_SCHEMA",
    "WORKING",
    "GeminiClient",
    "GeminiGate",
    "ModelGate",
    "OpenRouterClient",
    "PageReply",
    "Probe",
    "Usage",
    "process_gate",
]
