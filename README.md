# Kaagaz-OCR

Turns English business documents (spreadsheets, digital PDFs, scans, photos and handwritten pages) into typed PostgreSQL tables and searchable text, and manages the files. Every extracted value points back to the page and position it came from. A separate NL-to-SQL agent, built with Hermes, answers questions from those tables.

> **Status: ready for Day 1 of the five-day laptop build.** The repository holds the rules, the plan, CI and a bare API. Day 1 builds the skeleton described in [the Day 1 PRD](docs/prd/day-1-prd.html).

| Read | For |
|---|---|
| [Document-to-SQL Pipeline](https://claude.ai/artifact/7ATLDAJRxG3Qaab16NhjN8), summarised in [docs/architecture.md](docs/architecture.md) | The architecture and the decisions behind it |
| [Five-Day Laptop Build](https://claude.ai/artifact/4TmQt2EUoM784YqgWeyQUo) | The day-by-day plan |
| [docs/prd/](docs/prd/) | Each day's PRD |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How we work: the day loop, branches, pull requests and review |
| [CLAUDE.md](CLAUDE.md) | The rules for Claude Code, loaded every session |

## Who works on this

| Person | Owns |
|---|---|
| Vrushit Patel (PM) | Design, infrastructure, the database, security, each day's PRD, and every review and merge |
| Tushar (Engineer) | Building the pipeline day by day from the PRDs |

## Run it

You need Docker Desktop (with the WSL 2 backend on Windows), Git and [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 by itself.

```bash
uv sync                                       # the workspace and the dev tools, into .venv
uv run uvicorn kaagaz_api.main:app --reload   # the bare API at http://localhost:8000
```

The checks CI runs on Python, from the repository root:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Day 1 adds the rest of the stack (`docker compose up --build`) and updates this section.

## CI

A pull request into `main` needs four green checks and Vrushit's approval before it can merge. `main` is checked again after every merge and every Monday. The workflows in [.github/workflows/](.github/workflows/) explain each step.

- **Pull request:** the title, the branch name, the size, tests alongside code, suppressed checks and new dependencies.
- **Guardrails:** secrets in any commit (gitleaks), secret files, large files and documents, conflict markers, workflow safety (actionlint, zizmor), spelling and broken links between docs.
- **Python:** the lock file, ruff, strict mypy, dependency licences, known vulnerabilities (pip-audit), migrations, and pytest.
- **Stack:** the Compose file, pinned image versions, Dockerfile checks, the image builds, and the whole stack starting healthy.

## Licence

Apache 2.0. See [LICENSE](LICENSE).
