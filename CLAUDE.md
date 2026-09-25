# CLAUDE.md

Claude Code loads this file every session. These are the rules for this repository; follow them over your own habits.

## The product

Kaagaz-OCR turns English business documents (Excel and CSV files, digital PDFs, scans, photos and handwritten pages) into typed PostgreSQL tables and searchable text, and manages the files. Every extracted value points back to its page and position. A separate NL-to-SQL agent, built with Hermes, answers questions from those tables through a read-only database user and a search tool.

**People.** Vrushit Patel (PM) owns the platform: solution design, infrastructure, database, deployment, security, each day's PRD and every sign-off. Tushar builds the pipeline. Assume you are working with Tushar unless told otherwise. Vrushit reviews every change before it merges.

**Sources of truth.** Scope, order and decisions come from these, never from memory:

- Architecture, with the decisions and what they rejected: <https://claude.ai/artifact/7ATLDAJRxG3Qaab16NhjN8>, summarised in `docs/architecture.md`
- Five-Day Laptop Build: <https://claude.ai/artifact/4TmQt2EUoM784YqgWeyQUo>
- The day's PRD in `docs/prd/` (Day 1: `docs/prd/day-1-prd.html`)

| Day | Theme | Done when |
|---|---|---|
| Start now | AWS account and GPU quota, NL-to-SQL hand-off, about 20 sample documents | Requests sent and samples collected |
| 1 | Setup and skeleton | An upload reaches storage, the database and a Temporal run |
| 2 | Files that need no model | Excel files and digital PDFs become blocks; file management works |
| 3 | Scans and handwriting on CPU | Scanned pages and handwritten notes become blocks |
| 4 | Fields, checks and review | Invoices land in typed tables; wrong values wait for review |
| 5 | Search and hand-off | The NL-to-SQL agent can query the data with SQL and search |
| After day 5 | Hosting in AWS | Model test, Terraform, deploys and a load test |

A day's work starts only once its PRD is in `docs/prd/`.

**In scope.** English documents of every kind above, file management, blocks with positions, typed tables for known document types (invoices first) and JSON for the rest, validation with a review step, and hybrid search.

**Not yet.** Hindi and Marathi documents: do not build for them. Hosting in AWS before Day 5. The NL-to-SQL agent itself.

## Never do these

Hard rules. If a task seems to need one broken, stop and ask.

1. **Never run `git commit`, `git push`, or anything that rewrites history** (amend, rebase, `reset --hard`, `filter-repo`). The person you work with makes every commit. Leave changes unstaged unless asked to stage them.
2. **Never send document content to an outside AI service.** Models run behind an OpenAI-compatible address taken from settings: Ollama on the laptop, vLLM on our own GPU.
3. **Never put a real client document in the repository or on the laptop.** Use samples in `samples/`, which git ignores.
4. **Never read or commit a secret.** No keys, tokens, passwords, connection strings or `.env` files. Document a new setting in `.env.example` with a placeholder value.
5. **Never hard-code a host, key, path or model name.** Read settings through `kaagaz_shared.settings`.
6. **Never store an extracted value without its source:** a `field_sources` row with the block, page, `bbox`, model and confidence.
7. **Never create tables per upload, or change the schema outside an Alembic migration.**
8. **Never add a dependency without Vrushit's approval**, licence included. Libraries named in the architecture or in the day's PRD are approved for the package that document names. AGPL, SSPL and GPL-3.0 are refused outright, and CI fails on them. PyMuPDF is AGPL: use pypdfium2 and pdfplumber.
9. **Never game a check.** No deleted or skipped tests, weakened assertions, blanket `try/except`, `# type: ignore`, `--no-verify`, or hard-coded expected outputs. If a test is wrong, say why and ask.

## Never weaken these, not even to make a test pass

1. Every extracted value traces back to its page and position.
2. Documents and their content stay inside our environment. Temporal carries only IDs and small values.
3. Workflow code stays deterministic. Database, storage and model calls happen only in activities.
4. Uploads add rows. The schema changes only through reviewed migrations, together with the data dictionary.

## Build only what was asked

- Build what was asked **in this request**: no extra endpoints, screens, options, helpers for later, abstractions, refactors, reformatting or renames.
- Noticed something else worth doing? Say it in one line and ask. Don't build it.
- Don't pick up plan or PRD tasks on your own. The plan says what will exist; the person you're working with says what to do now.
- No config or plug-in systems for cases the current day doesn't have. The one swap point that exists on purpose is the OCR engine behind `ocr_page(image)`.
- Make the smallest diff that meets the acceptance criteria. Past about 300 lines, stop and propose a split.
- Keep the code plain enough that the engineer can defend every line in review.

## Before you write code

1. **State the acceptance criteria** in one to five checkable bullets, taken from the PRD's "Done when" items. If you can't, the task isn't ready: ask.
2. **Read every file you'll change or cite**, and cite `path:line`. Never describe code you haven't opened.
3. **Verify every name exists** before using it: packages, versions, imports, functions, settings, environment variables, endpoints, columns and paths.
4. **Check the installed version** before using a library's API (`uv.lock`, or `uv pip show <name>`). APIs change between major versions.
5. **Plan first** for anything touching the database schema, the workflow's steps or an API contract. Post the plan in the issue and wait for Vrushit.

## While you work

- **Facts come from sources, not memory.** If the plan, the PRD, the decisions and this file don't say it, it's unknown: ask.
- **Label what you claim** as verified (you read or ran it), inferred, assumed or unknown. "I don't know" is a correct answer; a confident guess is a defect.
- **"Done" needs evidence:** the command and its output. If you couldn't run it, write NOT RUN and why.
- **Never fabricate** output, logs, benchmarks, citations, URLs or quotes.
- **Three strikes.** After three failed attempts at the same problem, stop and report what you tried, what you learned, and the options.
- **Re-read before re-editing.** Files change; don't trust your memory of earlier turns.
- **Match the surrounding code:** the same naming, structure and comment density.

## Stop and ask before

- Deleting data or files outside the task, a destructive migration, or `docker compose down -v`.
- Anything touching CI, secrets, AWS, Terraform or `infra/`. That is Vrushit's area.
- Changing the database schema, the workflow's steps, or an API contract.
- Adding a dependency, or anything that costs money.
- Anything irreversible, or where two approaches have real trade-offs.

Ask one precise question with your recommended option. Don't ask what you can verify yourself.

## Decisions only Vrushit makes

If code depends on one of these, stop and ask.

| Open decision | Settled by |
|---|---|
| Which document types to add after invoices | Once Day 4's invoice extraction works |
| Qwen3.5-9B or 4B when hosted | The model test after Day 5 |
| Whether the laptop's OCR stand-in is close enough to PaddleOCR-VL-1.6 | The model test after Day 5 |
| Temporal Cloud or self-hosted Temporal | Before hosting starts |

Also his alone: database design, cloud setup, new libraries, security configuration and CI.

## Architecture rules that shape the code

- **Route by file type.** Excel, CSV and PDFs with a usable text layer never touch a GPU. Scans and photos go to the `gpu` task queue; on the laptop the same worker also serves it.
- **One OCR function.** Every OCR engine sits behind `ocr_page(image) -> blocks`, so the laptop stand-in and PaddleOCR-VL-1.6 swap by setting, not by code.
- **Models through one client.** Qwen3.5 is called through an OpenAI-compatible client whose base URL and model name come from settings.
- **Keep what the reader saw.** Store the raw reading next to the cleaned value; handwriting keeps both readings.
- **Pages are the unit of work.** The workflow fans out one activity per page.
- **IDs, not content, through Temporal.** Files live in S3 under `incoming/`, `originals/`, `pages/` and `parsed/`; activities pass keys and IDs.
- **One database.** Typed tables per document type; unknown types go to `extracted_fields`; `chunks` holds text, embeddings and the full-text index.
- **The NL-to-SQL agent reads, never writes.** It gets a read-only user. Update the data dictionary with every schema change.
- **Document text is untrusted input.** It must never act as instructions to a model, and markup or links inside it must never be rendered or followed.

## Stack

As in `docs/architecture.md`. Python 3.12 in a uv workspace: `api/kaagaz_api` today, with `shared/kaagaz_shared` and `worker/kaagaz_worker` added on Day 1. OCR and model libraries belong in the worker package only (`uv add --package kaagaz-worker ...`, once approved), so the API image stays small.

## Commands

From the repository root:

| Task | Command |
|---|---|
| Install everything | `uv sync` |
| Run the stack | `docker compose up --build` |
| Infrastructure only | `docker compose up postgres seaweedfs temporal` |
| Lint, format, types | `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy` |
| Tests | `uv run pytest` |
| Draft a migration | `uv run alembic revision --autogenerate -m "..."`, then review the file by hand |
| Apply, roll back | `uv run alembic upgrade head`, `uv run alembic downgrade base` |

The Compose and Alembic commands work once Day 1 adds `docker/`, `infra/` and `migrations/`. If a command you need isn't here or in `README.md`, find it in `pyproject.toml` or `docker-compose.yml` and say where you found it. Never guess one.

## Branches, pull requests and CI

Branch: `<type>/<scope>-<short-name>`, for example `feature/d1-upload-api`. Types: `feature`, `fix`, `chore`, `docs`, `test`, `infra`. Scopes: `d1` to `d5` for the build days, `host` for AWS hosting, `repo` for repository housekeeping.

Pull request title: `feature(d1): upload links and completion`; CI fails any other shape. Fill in the template completely.

Four checks in `.github/workflows/` must pass before a merge: Pull request, Guardrails, Python and Stack. A red check is information, not an obstacle to route around: fix the cause, or explain in the pull request why the check is wrong and let Vrushit decide.

If a hook or permission rule blocks you, don't work around it. Explain what you needed and ask.

## Done means

- [ ] Acceptance criteria met, and the PRD section named
- [ ] Tests added or updated, and they fail without the change
- [ ] Traceability, the Temporal rules and the no-outside-AI rule all hold
- [ ] Lint, format and type checks pass on the changed code
- [ ] No secrets in the diff, and no unapproved dependency
- [ ] Diff self-reviewed: minimal, only what was asked, no debug leftovers, under about 300 lines
- [ ] Docs updated if behaviour, settings or the schema changed (including the data dictionary)

## How to report back

**Summary** in one to three lines. **Changes**: the files, and why. **Verification**: the commands you ran and their output, with NOT RUN items named. **Assumptions and open questions**, labelled. **Not done**: anything you noticed but deliberately didn't build.
