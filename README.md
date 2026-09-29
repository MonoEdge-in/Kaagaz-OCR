# Kaagaz

Upload your business documents, ask questions in plain chat, and get answers that point back to the exact page they came from.

Kaagaz reads scanned and digital documents (invoices, registers, receipts, statements, forms), understands what is in them, and answers questions about them. Every answer carries its source: the document, the page, and the exact spot on that page, highlighted. When the answer is not in the documents, Kaagaz says so instead of inventing one.

> **Status: Sprint 1, Foundation.** Nothing here is production ready yet. See the delivery plan for what is being built when: https://claude.ai/artifact/Jeebdv68HRvxr8rTMYKe6s

---

## Who works on this

| Person | Role | Owns |
|---|---|---|
| Vrushit Patel | Product Manager | Solution design, infrastructure, database, deployment, security, all sign offs |
| Tushar | Engineer | The application: everything a user touches, from upload through to the chat screen |

---

## What it does, and does not do, at launch

**In scope**

- Documents in English, any file type: scans, photos, PDFs, spreadsheets.
- Chat that understands Hindi written in English letters, for example "Kiska profit sabse jyada tha last month?"
- Every answer carries its source page, highlighted.
- Answers labelled either **verified** or **needs review**, never silently guessed.
- Several customer companies on one system, fully separated from each other.
- A screen where a person corrects a mistake and the system remembers it.

**Not at launch**

- Hindi or Devanagari documents uploaded for reading.

---

## How a file becomes an answer

```
Upload (fingerprint, skip duplicates) -> Virus scan -> Prepare (page sizes and page images)
       -> Read (direct extraction, or the reader plug socket for scans)
       -> Agree and validate -> Index (text pieces, coordinates, search)

Question -> Retrieve (exact + fuzzy + meaning) -> Enough evidence?
         -> Draft with citations -> Verify every claim -> Answer with sources
```

Two rules hold the whole thing up:

1. **No source, no answer.** A value without page coordinates cannot be verified, so it is never shown as fact.
2. **Unreadable is a valid result.** A reader may refuse rather than guess.

## Product and system design

- [docs/architecture.md](docs/architecture.md): the system design and every decision, in one file.
- [docs/running-locally.md](docs/running-locally.md): running the product on a laptop, and checking each part works.

---

## Stack

Locked:

| Layer | Choice |
|---|---|
| Cloud | AWS, Mumbai (ap-south-1) |
| API | Python, FastAPI |
| Frontend | Next.js, PDF.js for the page viewer |
| Database | PostgreSQL with pgvector, full text search, fuzzy matching |
| Files | S3, one prefix per customer |
| Jobs | SQS with worker processes |
| Infrastructure as code | AWS CDK |

Still open, tracked in [docs/architecture.md](docs/architecture.md):

- Which AI reads scanned pages.
- The second, independent reader used for cross checking.

---

## Repository layout

```
.claude/          Shared Claude Code config: permission rules, guard hooks, skills
.github/          Pull request template, issue templates, CI, code owners
backend/          FastAPI application, workers, readers, database migrations
frontend/         Next.js application and the page viewer
infra/            Local stand-in config; AWS CDK definitions later
docs/             architecture.md (design and decisions), running-locally.md
```

---

## Getting started

Full steps, ports and the no-Docker path: [docs/running-locally.md](docs/running-locally.md).

```
cp .env.example .env        # then set SESSION_SECRET and TOTP_ENCRYPTION_KEY, 32+ random characters each
docker compose up --build   # open http://localhost:3000
docker compose exec -e KAAGAZ_DEMO_PASSWORD='<12+ characters>' api python -m kaagaz.cli seed-demo
```

Tests: `pytest -m "not db"` from `backend/` needs no database; the full `pytest`, and most of
`pytest -m isolation`, need `DATABASE_URL`. From `frontend/`: `npm test`.

---

## What CI checks on every pull request

Two workflows run on every pull request into `main`: `ci.yml` checks the code, and `pr.yml` checks the pull request itself.

**Guardrails, on every pull request, under a minute**

- No `.env`, `.pem`, `.p12`, `id_rsa` or keystore files committed
- No secret values added in the diff: AWS access keys, private key blocks, GitHub tokens, API keys, or a password inside a database connection string
- No leftover merge conflict markers
- No file over 2MB, because customer documents belong in storage and stay in git forever once committed

**The pull request itself (`pr.yml`)**

- Fails if the title is not in the form `feat(s1): short description`, or if more than 1000 lines changed (lock files excluded)
- Warns on: the branch name, no linked issue, code changed without tests, suppressed checks, new dependencies, and anything shaped like an Aadhaar or PAN number

**Backend**

- Runs against a real PostgreSQL 17 with pgvector, not a stand in, because isolation tests against a fake database prove nothing
- **Customer isolation tests run first and on their own**, so a failure is the first thing you see. Any test proving customer A cannot reach customer B's data carries the pytest marker `isolation`
- **Dependency licences are checked and the build fails on AGPL, SSPL or GPL v3**, so a library that makes the product impossible to sell commercially can never be added quietly
- Lint with `ruff check`, formatting with `ruff format --check`, types with `mypy`
- Migrations applied from a completely empty database, so a broken migration is caught before it reaches a real one
- The full `pytest` suite

**Frontend**

- `npm ci`, lint, typecheck, tests
- A real production build, because an app that passes its tests and fails to build is a common and expensive surprise

> **Green CI is required to merge.** A ruleset on `main` rejects direct pushes and blocks a pull request until CI passes and Vrushit has approved it.

---

## How we work

**Read [CONTRIBUTING.md](CONTRIBUTING.md) before you do anything else.** It is the full process, stage by stage: understanding a sprint, writing the PRD, getting it approved, picking up an issue, branching, building, testing, opening the pull request, review, merge, and sign off. It also lists the rules that are never bent, and what to do when you are stuck.

If you use Claude Code here, [CLAUDE.md](CLAUDE.md) loads automatically and carries the same rules in a form the assistant follows. The `/pr-check` skill checks your branch before you open a pull request.

---

## Licence

Apache 2.0. See [LICENSE](LICENSE).
