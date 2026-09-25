#!/usr/bin/env python3
"""Fail CI when an installed package's licence would make Kaagaz-OCR unsafe to sell.

Run it inside the project environment, from the repository root:

    uv run python .github/scripts/check_licences.py

It prints every installed package with its licence, then fails on:

- AGPL, SSPL or GPL-3.0, unless the package also offers a permissive choice
  ("MIT OR GPL-3.0" passes, because we take it under MIT);
- a package that declares no licence at all.

Licences are read from each package's own metadata: License-Expression first,
then the licence classifiers, then the free-text License field. Our own
packages declare license = "Apache-2.0" in their pyproject.toml.
"""

import re
import sys
from importlib.metadata import Distribution, distributions

# Packages Vrushit has reviewed by hand: normalised name -> reason and date.
# Only he approves changes here, because CODEOWNERS covers .github/.
REVIEWED: dict[str, str] = {}

FORBIDDEN = re.compile(
    r"affero|agpl|sspl|server side public licen[cs]e"
    r"|gnu general public licen[cs]e v3|(?<![a-z])gpl[ -]?v?3",
    re.IGNORECASE,
)
NO_LICENCE = {"", "unknown", "unlicensed", "none"}


def declared_licence(dist: Distribution) -> str:
    metadata = dist.metadata
    expression = (metadata.get("License-Expression") or "").strip()
    if expression:
        return expression
    classifiers = [
        line.split("::")[-1].strip()
        for line in metadata.get_all("Classifier") or []
        if line.startswith("License ::") and line.strip() != "License :: OSI Approved"
    ]
    if classifiers:
        # Several licence classifiers mean the package is offered under each of them.
        return " OR ".join(classifiers)
    text = (metadata.get("License") or "").strip()
    # Some packages paste their whole licence here; the first line names it.
    return text.splitlines()[0][:120] if text else ""


def strip_outer_brackets(text: str) -> str:
    text = text.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        for index, char in enumerate(text):
            depth += (char == "(") - (char == ")")
            if depth == 0 and index < len(text) - 1:
                return text  # the first bracket closes early: not one outer group
        text = text[1:-1].strip()
    return text


def choices(licence: str) -> list[str]:
    """The licences we may choose between: the top-level parts of "A OR B"."""
    options, current, depth = [], "", 0
    for part in re.split(r"(\(|\)|\s+OR\s+)", strip_outer_brackets(licence), flags=re.I):
        depth += (part == "(") - (part == ")")
        if depth == 0 and re.fullmatch(r"\s+OR\s+", part, flags=re.I):
            options.append(current)
            current = ""
        else:
            current += part
    options.append(current)
    return [option.strip() for option in options if option.strip()]


def problem(licence: str) -> str | None:
    if licence.strip().lower() in NO_LICENCE:
        return "declares no licence"
    if all(FORBIDDEN.search(option) for option in choices(licence)):
        return "is AGPL, SSPL or GPL-3.0, which makes a commercial product unsafe"
    return None


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> int:
    packages = sorted(
        {
            (dist.metadata["Name"], dist.version, declared_licence(dist))
            for dist in distributions()
            if dist.metadata["Name"]
        },
        key=lambda package: normalise(package[0]),
    )

    print("Installed packages and their licences:")
    failures = []
    for name, version, licence in packages:
        print(f"  {name} {version}: {licence or '(none declared)'}")
        reason = problem(licence)
        if reason and normalise(name) in REVIEWED:
            print(f"    reviewed by Vrushit: {REVIEWED[normalise(name)]}")
        elif reason:
            failures.append(f"{name} {version} {reason} ({licence or 'nothing declared'}).")

    for failure in failures:
        print(f"::error::{failure}")
    if failures:
        print("Don't merge. Raise it with Vrushit before adding or keeping this package.")
        return 1
    print(f"All {len(packages)} package licences are safe for a commercial product.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
