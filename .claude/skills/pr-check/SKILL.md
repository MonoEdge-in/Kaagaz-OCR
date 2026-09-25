---
name: pr-check
description: Check the current branch against Kaagaz-OCR's pull request rules before it goes to Vrushit for review. Covers size, one job per pull request, the PRD's Done when criteria, tests, migrations, traceability of extracted values, the Temporal rules, settings, outside AI calls, dependencies and licences, secrets and real documents. Use when someone says pr check, ready for PR, can I raise the PR, or before any pull request is opened. Reports READY or FIX FIRST.
---

# PR check

A fast, evidence-based gate, so Vrushit's review goes on judgement rather than rule breaks.

**Report only.** Don't fix anything unless asked. Never commit, never push, and never open the pull request.

## 1. Scope the diff

- Run `git status`, `git diff --stat main...HEAD` and the full `git diff main...HEAD`.
- Get two things from the person, the pull request draft or the issue: **what was asked**, and **which issue it closes**. Then open the day's PRD in `docs/prd/` and find the section the task comes from.

## 2. Run the checks

| # | Check | How | Fails when |
|---|---|---|---|
| 1 | Size | added plus removed lines from `--stat`, counting `uv.lock` separately | over about 300 lines of hand-written code |
| 2 | One job | read the diff against the stated task | the changes serve more than one purpose |
| 3 | Scope | every changed hunk maps to what was asked | extra features, refactors, reformatting, or files nobody asked for |
| 4 | Acceptance criteria | the PRD's "Done when" items, in the issue or the pull request | missing, or not checkable by someone else |
| 5 | Tests ship with it | new or changed tests in the diff cover the criteria | behaviour changed with no test |
| 6 | Tests pass | `uv run pytest`, with the output shown | a failure, or NOT RUN with no reason |
| 7 | Lint, format, types | `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` | errors in the changed files |
| 8 | Migrations | if `migrations/` changed: `uv run alembic upgrade head`, `downgrade base`, `upgrade head`; the data dictionary updated | a step fails, the migration was never reviewed by hand, or the data dictionary is stale |
| 9 | Traceability | every new or changed place that stores an extracted value | a value stored without a `field_sources` row carrying block, page, `bbox`, model and confidence |
| 10 | Temporal rules | `worker/kaagaz_worker/workflows.py` and every activity signature in the diff | I/O in workflow code, or file contents rather than IDs in a payload |
| 11 | Settings | search the diff for hosts, ports, URLs, keys, paths and model names | anything hard-coded, or a new setting missing from `.env.example` and `kaagaz_shared.settings` |
| 12 | No outside AI | every model call in the diff | a call to anything but the OpenAI-compatible base URL from settings, or document content sent outside our environment |
| 13 | Dependencies | the diff of every `pyproject.toml` and `uv.lock` | an addition without Vrushit's approval recorded (name and licence), or OCR and model libraries outside the worker package |
| 14 | Secrets, documents, leftovers | search for keys, tokens, `.env` values, real documents, `print`, `breakpoint`, commented-out code and TODOs with no issue | any found |
| 15 | Gaming a check | skipped or deleted tests, weakened assertions, `# type: ignore`, blanket `try/except`, `--no-verify` | any found |

Skip rows that don't apply and say why, for example "8 not applicable: no migration in this diff". If a command doesn't exist yet, write `NOT RUN: no <x> command configured`.

**Never mark a row as passed without evidence:** a command and its output, or a `path:line`.

## 3. Report

```
PR CHECK   <branch> into main        Closes #<issue>        PRD: <day and section>

| # | Check | Result | Evidence |

BLOCKERS:
WARNINGS:
VERDICT: READY | FIX FIRST
```

## 4. If READY

Draft the pull request description by filling in `.github/pull_request_template.md`: what changed and why, the issue it closes, the day and PRD section, the acceptance criteria, how it was tested (commands and their output), and what Vrushit should look at closely, such as migrations, traceability or a new dependency.

Hand the text over. The person opens the pull request.
