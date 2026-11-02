"""Mistral OCR, the second page reader the super admin can choose per organisation (#473).

Tushar, 2 Nov 2026: the operator console chooses who reads an organisation's scanned pages,
Gemini through OpenRouter or Mistral OCR, and which Mistral model. What is chosen is what is
used: an organisation on Mistral never has a page read by Gemini, not even when Mistral is out
of credit or down. Its documents wait and are read by Mistral once it works again.

Mistral counts as set up on a server only with MISTRAL_API_KEY and MISTRAL_ZDR_APPROVED=true
(zero data retention confirmed with Mistral; council 2 Nov 2026). Only ``/v1/ocr`` is called:
zero data retention does not cover Mistral's Files or Batch endpoints.

Stdlib only, so the API can import the model list without the reader's plumbing. The reader
itself, ``MistralClient`` (one POST to ``/v1/ocr`` a page) and ``page_read`` (a reply page as a
``PageRead``), is in ``kaagaz.reading.mistral_reader``, which imports the Gemini reader's
pinning and so is never loaded by the API (#473, 2 Nov 2026).
"""

from dataclasses import dataclass

MISTRAL = "mistral"
OPENROUTER = "openrouter"
SCAN_READERS = (OPENROUTER, MISTRAL)

MISTRAL_OCR_URL = "https://api.mistral.ai/v1/ocr"
# The most one page's call may take, capped by the page's time left on the engine's clock.
MISTRAL_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class MistralModel:
    """One Mistral OCR model the console offers. ``has_blocks``: the reply says where each
    paragraph is on the page; without it no line is pinned to a spot."""

    id: str
    name: str
    usd_per_page: float
    has_blocks: bool


# Prices from docs.mistral.ai/inference/pricing, 2 Nov 2026. Never "mistral-ocr-latest": it moves
# to each new model, and with it the price. Migration 0065's CHECK lists the same ids.
MISTRAL_OCR_MODELS: dict[str, MistralModel] = {
    "mistral-ocr-4-1": MistralModel("mistral-ocr-4-1", "Mistral OCR 4.1", 0.004, True),
    "mistral-ocr-2512": MistralModel("mistral-ocr-2512", "Mistral OCR 3", 0.002, False),
}
DEFAULT_MISTRAL_MODEL = "mistral-ocr-4-1"

# The ledger's settings pair for a Mistral read (model_usage.thinking / resolution and
# pages.read_thinking / read_resolution): Mistral has neither, and the scan-cost estimate
# matches rows on the pair, so it is always this one.
MISTRAL_THINKING = "none"
MISTRAL_RESOLUTION = "page"


def read_by(model_id: str) -> str:
    """The page's ``read_by`` and the ledger's model: "mistral/<id>". It holds a "/" on
    purpose: jobs/accuracy.py and jobs/land_facts.py treat such a name as a model's read."""
    return f"{MISTRAL}/{model_id}"
