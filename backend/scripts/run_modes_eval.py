"""Read documents in each reading mode and count the wrong values shown as Verified
(features 25-27: council 15 Oct 2026, C13 invariant 4 and C14).

    cd backend
    python -m scripts.run_modes_eval --fake
    python -m scripts.run_modes_eval --truth TRUTH.json --careful ID --others ID[,ID] [--docs DIR]

Each document is read three times by the real pipeline, with the in-memory stand-ins of
tests/test_pipeline_gemini.py (no database): Single (the main model), Dynamic (unclear
pages read again by ``--careful``) and Ensemble (``--others`` vote, 2 of 3). It prints a
row per mode: values read, Verified, wrong and Verified, Needs review, abstain (a truth
value nothing was read for), not in the truth, Verified precision and tokens. It exits 1
when Ensemble has more wrong-and-Verified values than Single, or Dynamic's Verified
precision is below Single's.

``--fake`` reads tests/fixtures/modes_eval_fake.json: scripted model and checker
readings of one-line scans, C14's slices (3/8, 1/7, 0/O, a stamp over digits). No
network, so CI can run it. Without it, every PDF and image in ``--docs`` (default the
golden scans, tests/golden/xlang) is read by the Gemini reader with real models through
OpenRouter and the real RapidOCR checker. That costs money, so run it by hand only, with
the stack's settings in the shell (OPENROUTER_API_KEY among them), as for ``kaagaz.cli``.
``--truth`` is JSON ``{"file name": {"field": "text"}}``, typed by a person from the page
images only (C14), never from a model's reading.
"""

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, TextIO, cast

from kaagaz.db.models import Agent, Reading
from kaagaz.extraction.labels import comparable
from kaagaz.extraction.quotes import normalise_quote
from kaagaz.extraction.values import ExtractedValue
from kaagaz.pipeline import JobReading

BACKEND = Path(__file__).resolve().parent.parent
FAKE_CASES = BACKEND / "tests" / "fixtures" / "modes_eval_fake.json"
GOLDEN_SCANS = BACKEND / "tests" / "golden" / "xlang"
MODES = ("single", "dynamic", "ensemble")
# --fake's models, by the fixture's names for them.
FAKE_MODELS = {
    "standard": "google/gemini-3.8-flash",
    "careful": "google/gemini-3.1-pro-preview",
    "second": "anthropic/claude-sonnet-5.5",
    "third": "google/gemini-3.1-pro",
}
SCANS = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")

Truth = dict[tuple[str, str], str]  # (document, field) -> what the page prints


@dataclass(frozen=True, slots=True)
class Read:
    """One value the pipeline saved."""

    document: str
    field: str
    text: str
    label: str


@dataclass(slots=True)
class Tally:
    values: int = 0
    verified: int = 0
    wrong_verified: int = 0
    needs_review: int = 0
    abstain: int = 0
    unjudged: int = 0  # values whose (document, field) the truth does not have
    tokens: int = 0

    @property
    def precision(self) -> float | None:
        """Share of Verified values that are right; None with none Verified."""
        return None if self.verified == 0 else (self.verified - self.wrong_verified) / self.verified


def same(a: str, b: str) -> bool:
    """The checker's own equality (``labels._same``): ``comparable`` and the D2 normalising."""
    return normalise_quote(comparable(a)) == normalise_quote(comparable(b))


def tally(reads: Iterable[Read], truth: Truth, tokens: int = 0) -> Tally:
    """Score one mode's values against ``truth``. A value is wrong when its text is not the
    truth for its document and field; every value of a field is judged on its own."""
    counts, seen = Tally(tokens=tokens), set()
    for read in reads:
        key = (read.document, read.field)
        seen.add(key)
        counts.values += 1
        counts.unjudged += key not in truth
        if read.label == "verified":
            counts.verified += 1
            counts.wrong_verified += key in truth and not same(read.text, truth[key])
        else:
            counts.needs_review += 1
    counts.abstain = len(truth.keys() - seen)
    return counts


def problems(tallies: Mapping[str, Tally]) -> list[str]:
    """The broken invariants: Ensemble's wrong-and-Verified above Single's (invariant 4),
    Dynamic's Verified precision below Single's."""
    single, dynamic, ensemble = (tallies[mode] for mode in MODES)
    found = []
    if ensemble.wrong_verified > single.wrong_verified:
        wrong = f"{ensemble.wrong_verified} wrong values as Verified, Single {single.wrong_verified}"
        found.append(f"Ensemble shows {wrong}")
    if (
        dynamic.precision is not None
        and single.precision is not None
        and dynamic.precision < single.precision
    ):
        found.append(
            f"Dynamic's Verified precision {dynamic.precision:.1%} is below Single's {single.precision:.1%}"
        )
    return found


def report(tallies: Mapping[str, Tally]) -> str:
    names = [item.name for item in fields(Tally)]
    rows = [f"{'mode':<9}" + "".join(f"{name:>15}" for name in names) + f"{'precision':>11}"]
    for mode, counts in tallies.items():
        precision = "-" if counts.precision is None else f"{counts.precision:.1%}"
        rows.append(f"{mode:<9}" + "".join(f"{getattr(counts, n):>15}" for n in names) + f"{precision:>11}")
    return "\n".join(rows)


def reading(mode: str, lead: Agent, careful: Agent | None, others: Sequence[Agent], thinking: str) -> Reading:
    if mode == "dynamic":
        return Reading("dynamic", lead, careful=careful, thinking=thinking)
    if mode == "ensemble":
        return Reading("ensemble", lead, others=tuple(others), thinking=thinking)
    return Reading("single", lead, thinking=thinking)


def read_once(system: Any, data: bytes, name: str) -> tuple[list[Read], int]:
    """Run ``data`` through the rig ``system``: its saved values and the tokens billed."""
    from kaagaz.jobs.worker import JobFailed
    from tests.test_pipeline_gemini import CUSTOMER

    job = system.job(data)
    try:
        system.processor(job, lambda: None)
    except JobFailed as failed:
        if failed.code != "unreadable":  # an outage or a bug stops the eval, never counts as abstain
            raise
        # An unreadable document saves no values: its truth counts as abstain.
    values: list[ExtractedValue] = system.store.values_for(CUSTOMER, job.document_id)
    (run,) = system.runs.for_document(job.customer_id, job.document_id)
    tokens = 0 if run.totals is None else run.totals.input_tokens + run.totals.output_tokens
    return [Read(name, item.field, item.value_text, item.label) for item in values], tokens


def run_modes(
    documents: Sequence[tuple[str, bytes]],
    build: Callable[[str, str, Reading], Any],
    readings: Mapping[str, Reading],
) -> dict[str, tuple[list[Read], int]]:
    """Each document read in each mode; ``build(name, mode, reading)`` gives a fresh rig, so
    no page cache is shared between modes."""
    out: dict[str, tuple[list[Read], int]] = {}
    for mode, chosen in readings.items():
        reads, tokens = [], 0
        for name, data in documents:
            got, used = read_once(build(name, mode, chosen), data, name)
            reads += got
            tokens += used
        out[mode] = (reads, tokens)
    return out


def fake_run(path: Path = FAKE_CASES) -> tuple[dict[str, tuple[list[Read], int]], Truth]:
    """--fake: the fixture's cases, each a one-page scan, through the real pipeline with
    the scripted models and checker (tests/test_dynamic_reading.py's fakes)."""
    from kaagaz.ai.replies import ModelOutputError
    from tests.pdf_builder import make_pdf
    from tests.test_dynamic_reading import FOUND, TOTAL_BOX, ModelClient, engine
    from tests.test_gemini_ocr import FakeFinder, line, reply, value
    from tests.test_pipeline_gemini import SHARP, FakeChecker, rig

    cases = {case["name"]: case for case in json.loads(path.read_text(encoding="utf-8"))["cases"]}
    truth = {(name, field): text for name, case in cases.items() for field, text in case["truth"].items()}
    agents = {role: Agent(model, "google-vertex", role) for role, model in FAKE_MODELS.items()}

    def page_reply(read: Mapping[str, Any] | None) -> Any:
        if read is None:
            return ModelOutputError("invalid_output")  # the model failed on the page
        text = read["line"]
        unsure = (text.split(": ", 1)[1],) if read.get("unsure") else ()
        return reply(
            value(
                line(text, TOTAL_BOX, unsure=unsure),
                quality=read.get("quality", "good"),
                issues=tuple(read.get("issues", ())),
            )
        )

    def build(name: str, mode: str, chosen: Reading) -> Any:
        case = cases[name]
        client = ModelClient({FAKE_MODELS[role]: page_reply(case["reads"].get(role)) for role in FAKE_MODELS})
        return rig(
            engine(client, finder=FakeFinder(FOUND), ocr_engine="gemini"),
            checker=FakeChecker(case["checker"]),
            ocr_model=chosen.lead.model,
            job_reading=lambda customer, document: JobReading(
                None, chosen.lead.model, chosen.lead.provider, reading=chosen
            ),
        )

    readings = {
        mode: reading(mode, agents["standard"], agents["careful"], (agents["second"], agents["third"]), "low")
        for mode in MODES
    }
    pdf = make_pdf([SHARP])
    return run_modes([(name, pdf) for name in cases], build, readings), truth


def live_run(args: argparse.Namespace) -> tuple[dict[str, tuple[list[Read], int]], Truth]:
    """Real models and the real checker over ``--docs``; see the module notes."""
    from kaagaz.reading.gemini_ocr import GeminiEngine
    from kaagaz.reading.rapid_pool import shared_pool
    from kaagaz.settings import Settings
    from tests.test_pipeline_gemini import rig

    settings = Settings.from_env(os.environ)
    if settings.openrouter_api_key is None:
        raise SystemExit("OPENROUTER_API_KEY is not set: run with --fake, or load the stack's settings")
    raw = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    truth = {(name, field): text for name, values in raw.items() for field, text in values.items()}
    documents = [
        (path.name, path.read_bytes())
        for path in sorted(Path(args.docs).iterdir())
        if path.suffix.lower() in SCANS
    ]
    if not documents:
        raise SystemExit(f"no scans in {args.docs}")
    provider = sorted(settings.openrouter_providers)[0]  # ponytail: first allowed provider for every model
    lead = Agent(settings.ocr_model, provider, "main")
    careful = Agent(args.careful, provider, "careful")
    others = [Agent(model, provider, "other") for model in args.others.split(",")]
    pool = shared_pool(settings.cpu_workers)

    def build(name: str, mode: str, chosen: Reading) -> Any:
        return rig(
            GeminiEngine.from_settings(settings, pool=pool),
            checker=cast(Any, pool),  # the rig types its fakes; the processor takes any Checker
            settings=settings,
            ocr_model=lead.model,
            job_reading=lambda customer, document: JobReading(
                None, lead.model, lead.provider, reading=chosen
            ),
        )

    readings = {mode: reading(mode, lead, careful, others, settings.ocr_thinking_level) for mode in MODES}
    return run_modes(documents, build, readings), truth


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(description="Wrong-and-Verified values per reading mode")
    parser.add_argument("--fake", action="store_true", help="scripted readings, no network")
    parser.add_argument("--docs", default=str(GOLDEN_SCANS))
    parser.add_argument("--truth", help="JSON {file: {field: text}}, typed from the images")
    parser.add_argument("--careful", help="Dynamic's model for unclear pages")
    parser.add_argument("--others", help="Ensemble's one or two other models, comma separated")
    args = parser.parse_args(argv)
    if not args.fake and not (args.truth and args.careful and args.others):
        parser.error("without --fake, --truth, --careful and --others are needed")
    if args.others and len(args.others.split(",")) > 2:
        parser.error("--others takes one or two models (Ensemble votes 2 of 3)")
    runs, truth = fake_run() if args.fake else live_run(args)
    tallies = {mode: tally(reads, truth, tokens) for mode, (reads, tokens) in runs.items()}
    broken = problems(tallies)
    out.write(report(tallies) + "\n" + "".join(f"BROKEN: {problem}\n" for problem in broken))
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
