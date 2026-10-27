"""Position recall (#381, lost in the middle): does Ask find a fact wherever it sits in a long
document?

    cd backend
    python -m scripts.eval_position_recall --fake
    python -m scripts.eval_position_recall [--sizes 20000,80000] [--docs 2] [--target 0.9]
        [--precision-model ID]

Each synthetic document is pages of plain filler with three facts planted in it: one near the
start, one in the middle and one near the end (a code no filler holds, on a page of its own). Each
fact is asked for on its own through the real ``AnswerService`` (rewrite, ranking, read in parts,
draft, verify and the final check), Swift (medium) and Precision (high), with the document as the
chat's whole scope through in-memory stand-ins for the database (tests/test_answering_context.py:
the pieces, and a word search for the ranking and the search route). A fact counts as found when
an answered sentence holds its code and cites its page. It prints the recall of each position per
size and mode, and exits 1 when the middle's recall of any row is below ``--target``.

``--fake`` answers with a scripted model that reads every block it is given (no network), so it
checks the plumbing only and always finds every fact. Without it the models are real, through
OpenRouter: that costs money, so run it by hand only, with the stack's settings in the shell
(OPENROUTER_API_KEY among them), as for ``kaagaz.cli``. Swift runs on MODEL_CHAT; Precision on
``--precision-model`` (default MODEL_CHAT; the product's Precision uses the strongest allowed one).
"""

import argparse
import dataclasses
import os
import random
import re
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from kaagaz.ai.client import ModelClient
from kaagaz.db.models import Agent, ChatChoice
from kaagaz.settings import Settings

POSITIONS = ("start", "middle", "end")
PAGE_CHARS = 1600  # about 400 tokens of filler a page
# (what the fact says, its code's prefix, the question that asks for it): no filler word is in them.
FACTS = (
    (
        "The locker code for the Pune warehouse is {code}.",
        "PX",
        "What is the locker code for the Pune warehouse?",
    ),
    ("The gate pass for the Nashik depot is {code}.", "GN", "What is the gate pass for the Nashik depot?"),
    (
        "The vault combination at the Thane office is {code}.",
        "VT",
        "What is the vault combination at the Thane office?",
    ),
)
_FILLER = (
    "The parties record that routine matters of correspondence are kept in the register.",
    "Each schedule is read with the general terms and the annexures that follow it.",
    "Notices are sent by registered post and are taken as received after seven days.",
    "Headings are for convenience only and do not change the meaning of any clause.",
    "Any change to these terms is made in writing and signed by both of the parties.",
)
_CODE = re.compile(r"\b[A-Z]{2}-\d{4}\b")


@dataclass(frozen=True, slots=True)
class Planted:
    position: str
    page: int
    code: str
    question: str


def document(tokens: int, seed: int) -> tuple[list[str], list[Planted]]:
    """The pages of a document of about ``tokens`` tokens (at least 10 pages), and its three
    facts: on the page at 5%, 50% and 95% of it."""
    rng = random.Random(seed)  # noqa: S311 - a fixed seed makes test documents, not a secret
    count = max(10, tokens * 4 // PAGE_CHARS)
    pages = []
    for n in range(1, count + 1):
        lines = [f"Page {n}."]
        while sum(len(line) + 1 for line in lines) < PAGE_CHARS:
            lines.append(rng.choice(_FILLER))
        pages.append("\n".join(lines))
    planted = []
    for position, (said, prefix, question), share in zip(POSITIONS, FACTS, (0.05, 0.5, 0.95), strict=True):
        page = min(count, max(1, round(count * share)))
        code = f"{prefix}-{rng.randrange(1000, 10000)}"
        pages[page - 1] = f"{pages[page - 1]}\n{said.format(code=code)}"
        planted.append(Planted(position, page, code, question))
    return pages, planted


def found(answer: Any, fact: Planted) -> bool:
    """An answered sentence holds the fact's code and cites the page it is on."""
    if answer.status != "answered":
        return False
    pages = {c.n: c.page_no for c in answer.citations}
    return any(
        fact.code in s.text and any(pages.get(n) == fact.page for n in s.citations) for s in answer.sentences
    )


class ScriptedModel:
    """--fake: a draft cites the first block holding a code; a part names every block holding one.
    It reads every block it is given, so recall is 1: the plumbing only."""

    chat_model = "scripted"
    embed_model = "scripted"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.25, 0.5, 0.25] for _ in texts]

    def chat_json(self, *, system: str, user: str, schema: dict[str, Any], **_: Any) -> dict[str, Any]:
        from kaagaz.answering import parts, rewrite

        if system == rewrite.SYSTEM_PROMPT:
            return {
                "question": re.findall(r"<question>\n(.*?)\n</question>", user, re.DOTALL)[0],
                "aspects": [],
            }
        asked = re.findall(r"<question>\n(.*?)\n</question>", user, re.DOTALL)[0]
        prefix = next(prefix for _, prefix, question in FACTS if question == asked)
        held = [
            (int(n), code)
            for n, text in re.findall(r'<evidence id="(\d+)"[^>]*>\n(.*?)\n</evidence>', user, re.DOTALL)
            for code in _CODE.findall(text)
            if code.startswith(prefix)
        ]
        if system == parts.SYSTEM_PROMPT:
            return {"facts": [{"evidence": n, "quote": code} for n, code in held]}
        if not held:
            return {"status": "not_found", "sentences": []}
        n, code = held[0]
        return {"status": "answered", "sentences": [{"text": f"It is {code}.", "evidence": [n]}]}


def ask_all(
    model: ModelClient, settings: Settings, choices: dict[str, ChatChoice], sizes: Sequence[int], docs: int
) -> dict[tuple[int, str], dict[str, list[bool]]]:
    """Every fact of every document, asked in each mode: (size, mode) -> position -> found."""
    from kaagaz.answering.service import AnswerService
    from tests.test_answering_context import FakeContext, Fused, page
    from tests.test_answering_golden import NOW, UNUSED_DB, FakeFixedQueries, FakeUnchecked

    results: dict[tuple[int, str], dict[str, list[bool]]] = {}
    for size in sizes:
        for seed in range(docs):
            texts, planted = document(size, seed)
            name = f"synthetic-{size}-{seed}.pdf"
            hits = [page(n, text, document=name) for n, text in enumerate(texts, start=1)]
            customer = str(uuid.uuid5(uuid.NAMESPACE_URL, name))
            retriever = Fused([])
            retriever.pieces = {customer: hits}
            service = AnswerService(
                db=UNUSED_DB,
                model=model,
                settings=settings,
                clock=lambda: NOW,
                retriever=retriever,
                translated_search=retriever,
                fixed_queries=FakeFixedQueries({}),
                unchecked=FakeUnchecked(),
                context=FakeContext(hits),
            )
            for mode, choice in choices.items():
                row = results.setdefault((size, mode), {position: [] for position in POSITIONS})
                for fact in planted:
                    row[fact.position].append(
                        found(service.answer(customer, fact.question, choice=choice), fact)
                    )
    return results


def report(results: dict[tuple[int, str], dict[str, list[bool]]]) -> str:
    lines = [f"{'tokens':>8}  {'mode':<10}" + "".join(f"{position:>10}" for position in POSITIONS)]
    for (size, mode), row in sorted(results.items()):
        cells = "".join(f"{f'{sum(hits)}/{len(hits)}':>10}" for hits in row.values())
        lines.append(f"{size:>8}  {mode:<10}{cells}")
    return "\n".join(lines)


def below(results: dict[tuple[int, str], dict[str, list[bool]]], target: float) -> list[str]:
    """Each row whose middle recall is under ``target``."""
    return [
        f"{mode} at {size} tokens: middle recall {sum(row['middle'])}/{len(row['middle'])}"
        for (size, mode), row in sorted(results.items())
        if row["middle"] and sum(row["middle"]) / len(row["middle"]) < target
    ]


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(description="Recall of facts planted at the start, middle and end")
    parser.add_argument("--fake", action="store_true", help="a scripted model, no network")
    parser.add_argument("--sizes", default="20000,80000", help="document sizes in tokens, comma separated")
    parser.add_argument("--docs", type=int, default=2, help="documents of each size")
    parser.add_argument("--target", type=float, default=0.9, help="the least middle recall that passes")
    parser.add_argument("--precision-model", help="Precision's model (default MODEL_CHAT)")
    args = parser.parse_args(argv)
    sizes = [int(size) for size in args.sizes.split(",")]
    model: Any
    if args.fake:
        from tests.settings_factory import make_settings

        settings, model = make_settings(), ScriptedModel()
        swift = precision = Agent("scripted", "scripted", "Scripted")
    else:
        from kaagaz.ai.client import build_model_client

        settings = Settings.from_env(os.environ)
        if settings.openrouter_api_key is None:
            raise SystemExit("OPENROUTER_API_KEY is not set: run with --fake, or load the stack's settings")
        settings = dataclasses.replace(settings, model_enabled=True)
        model = build_model_client(settings)
        provider = sorted(settings.openrouter_providers)[0]  # ponytail: first allowed provider
        swift = Agent(settings.model_chat, provider, "Swift")
        precision = Agent(args.precision_model or settings.model_chat, provider, "Precision")
    choices = {"swift": ChatChoice([swift], "medium"), "precision": ChatChoice([precision], "high")}
    results = ask_all(model, settings, choices, sizes, args.docs)
    broken = below(results, args.target)
    out.write(report(results) + "\n" + "".join(f"BELOW TARGET: {line}\n" for line in broken))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
