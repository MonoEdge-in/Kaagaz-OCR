"""Production QA S3: the backend had no lock file, so each image build took the newest
versions pyproject.toml allows. The libraries that face the internet or read untrusted files,
and their direct dependencies, are pinned exactly in constraints.txt (the Dockerfile and CI
install with ``-c constraints.txt``), and this environment runs the pinned versions."""

import re
from importlib import metadata
from pathlib import Path

PINNED = [
    # the API: routing, request parsing, multipart uploads, the server
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic-core",
    "typing-extensions",
    "typing-inspection",
    "annotated-doc",
    "opentelemetry-api",
    "python-multipart",
    "uvicorn",
    "click",
    "h11",
    "anyio",
    "idna",
    # untrusted files and secrets
    "cryptography",
    "cffi",
    "pdfminer.six",
    "charset-normalizer",
    "pillow",
    # storage and the queue
    "boto3",
    "botocore",
    "jmespath",
    "s3transfer",
]


def _pins() -> dict[str, str]:
    text = (Path(__file__).resolve().parents[1] / "constraints.txt").read_text(encoding="utf-8")
    return {
        name.lower(): version
        for name, version in re.findall(r"^([A-Za-z0-9._-]+)==(\S+)", text, re.MULTILINE)
    }


def test_the_risky_libraries_are_pinned_exactly_and_installed_at_that_version() -> None:
    pins = _pins()
    assert [name for name in PINNED if name not in pins] == []
    assert {name: metadata.version(name) for name in PINNED} == {name: pins[name] for name in PINNED}
