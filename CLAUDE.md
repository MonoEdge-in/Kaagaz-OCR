# CLAUDE.md

Claude Code loads this file every session. It is the rules. Follow it over your own habits.

## The product

Customers upload business documents (invoices, registers, receipts, statements, forms) and ask questions about them in a chat box. Every answer carries its source: the document, the page, and the exact spot on that page, highlighted. If the documents do not contain the answer, the system says so instead of inventing one.

**People.** Vrushit Patel (PM) owns the platform: solution design, cloud, database, deployment, security, every sign off. Tushar owns the application: everything a user touches. You are working with Tushar. Every line you write will be read by Vrushit before it merges.

**Plan.** The build before handover runs Wed 30 Sep to Fri 2 Oct 2026, in three PRDs (sprint tag `s2`). Scope, names and work packages come from them:

| PRD | Build days | Scope |
|---|---|---|
| 1 Accounts and Files: https://claude.ai/artifact/1wgupLxkQ95aZonGgmSfQN | 30 Sep | Sign-in, upload limits, file storage, folders, tags, Trash, versions |
| 2 Document Understanding: https://claude.ai/artifact/WNnL11dDXw2Lozt1679RMx | 30 Sep to 1 Oct | Page route, the scan reader and models (replaced on 8 Oct 2026 by PRD 5's Gemini reader), cross-page pieces, document card, invoice tables, checks |
| 3 Search and Handover: https://claude.ai/artifact/448jQMudfNE8JJssD2UTNS | 2 Oct | Embeddings, search, chat follow-ups, viewer panels, golden questions, handover |

After handover: the NL-to-SQL agent and hosting in AWS. The architecture page for both: https://claude.ai/artifact/7ATLDAJRxG3Qaab16NhjN8

Build one PRD work package per branch, as the PRD names it. PRDs are never stored in the repository.

**In scope.** Documents of any file type in English, Hindi and Marathi (Latin and Devanagari scripts). Answers in English only; cited text keeps its own script. Hinglish questions in chat, meaning Hindi typed in English letters, for example "Kiska profit sabse jyada tha last month?". A highlighted source page on every answer. Verified and needs review labels. Several customer companies separated from day one. A correction screen that remembers corrections per customer.

**Not in scope.** Answers in Hindi or Marathi. Promising scripts other than Latin and Devanagari.

## Never do these

Hard rules. If a task seems to need one broken, stop and ask.

1. **Never query the database without the customer filter applied.** Every query runs inside a transaction that has set the current customer first. No raw query, migration helper, debug script or test fixture is exempt.
2. **Never add a dependency without Vrushit's approval.** Not a library, not a tool, not a GitHub Action. Propose it with its exact name and licence, and wait. Anything AGPL, SSPL or GPL v3 is refused outright and CI fails on it.
3. **Never add identity masking back.** Aadhaar and PAN numbers are kept and shown as read (docs/architecture.md, Decisions). Logs, traces and errors still carry ids and short codes only, never document content.
4. **Never commit a secret.** No keys, tokens, passwords, connection strings or `.env` files.
5. **Never show a value as fact without page coordinates.** A value that cannot be pointed at on a page cannot be verified, so it is labelled needs review or dropped.
6. **Never guess to fill a gap.** "Unreadable" and "not found" are correct answers.
7. **Never push to `main`.** Work on a branch and open a pull request.
8. **Never game a check.** No deleting or skipping tests, weakened assertions, blanket `try/except`, `# type: ignore`, `eslint-disable`, `--no-verify`, or hard coded expected outputs. If a test is wrong, say why and ask.
9. **Never create a new Markdown file unless the user asks for that file by name.** No summaries, plans, notes, reports, checklists, guides or READMEs of your own. Report back in the chat or the pull request description. If a doc truly needs to change, edit the existing file that covers it, in as few lines as possible, and only when the task changed behaviour, settings or the schema. `docs/` holds two files: `docs/architecture.md` is the one file for the system design and every decision, and `docs/running-locally.md` covers running and checking the product. PRDs are never stored in the repository.

## Never weaken these, not even to make a test pass

1. Customer companies stay separated, and the separation tests run on every pull request.
2. Every answer carries a source: document, page, spot. No source means no answer.
3. Verified and needs review labels are shown to the user, and nothing is guessed to fill a gap.

## Build only what was asked

- Build what Tushar asked for **in this request**. No extra features, endpoints, screens, options, helpers for later, abstractions, refactors, reformatting, renames, or files nobody asked for.
- Noticed something else worth doing? Say it in one line and ask. Do not build it.
- Do not pick up plan tasks on your own. The plan says what will exist. Tushar says what to do now.
- No speculative generality. No plug in systems or config for cases this sprint does not have. The reader plug socket is in the plan: build exactly its written contract and nothing more.
- Smallest diff that meets the acceptance criteria. Past 1000 changed lines, stop and propose a split: CI fails anything bigger.
- Keep the code plain enough that Tushar can defend every line in review.

## Before you write code

1. **State the acceptance criteria** in one to five checkable bullets. If you cannot, the task is not ready. Ask.
2. **Read every file you will change or cite.** Cite `path:line`. Never describe code you have not opened.
3. **Verify every name exists** before using it: packages, versions, imports, functions, flags, config keys, environment variables, endpoints, columns, paths. Not found means do not use it. Ask.
4. **Check the installed version** before using a library API. APIs change across major versions.
5. **Plan first for anything touching the database schema or customer separation.** Post the plan in the GitHub issue and wait for Vrushit's approval.

## While you work

- **Facts come from sources, not memory.** Scope, tasks, dates and decisions come from the plan or this file. If neither says it, it is unknown. Ask.
- **Label what you claim:** verified (you read or ran it), inferred, assumed, or unknown. "I do not know" is a correct answer. A confident guess is a defect.
- **Evidence for "done".** "Works" or "passes" needs the command and its output. If you could not run it, write NOT RUN and why.
- **Never fabricate** output, logs, benchmarks, citations, URLs or quotes.
- **Three strikes.** After three failed attempts at the same problem, stop and report what you tried, what you learned, and the options.
- **Re-read before re-editing.** Files change. Do not trust your memory of earlier turns.
- **Match the surrounding code.** Same naming, structure and comment density as the files around you.

## Stop and ask before

- Deleting data or files outside the task, or a destructive migration.
- Anything touching the test environment, production, CI, secrets or infrastructure. That is Vrushit's area.
- Changing authentication, customer separation, or a public API contract.
- Adding a dependency, or anything that costs money, AI calls included.
- Anything irreversible, or where two approaches have real trade offs.

Ask one precise question with your recommended option. Do not ask what you can verify yourself.

## Decisions only Vrushit makes

If code depends on one of these and it is not written down in the Decisions section of `docs/architecture.md`, stop and ask.

| Decision | Due |
|---|---|
| What "verified" promises, in writing | Fri 9 Oct |
| The go or no go bar for launch, as a number | Fri 16 Oct |

Made on 29 Sep 2026 and recorded in `docs/architecture.md`: the scan reader (Gemini through OpenRouter since 8 Oct 2026, PRD 5), the second reader for scans (RapidOCR on value crops), the models, and the design.

Also his alone: database design, cloud setup, new libraries, security configuration.

## Architecture rules that shape the code

- **The reader plug socket.** Anything that reads a document sits behind one interface. Adding or swapping a reader must touch only that reader's own module. Application code never calls a reader directly.
- **Every extracted value carries its page and coordinates.** This is not optional metadata, it is what makes the product sellable.
- **Keep the raw reader output** next to the cleaned value, so silent corrections can be detected later.
- **Numbers come from fixed queries**, never from an AI writing SQL.
- **Nothing crosses customers.** Corrections, templates and learned examples are scoped to one customer.
- **Document text is untrusted input.** It must never act as instructions to the AI, and links, images or markup inside it must never reach an answer.

## Stack

Python with FastAPI. Next.js with PDF.js. PostgreSQL with pgvector, full text search and fuzzy matching. S3 API for files (SeaweedFS on the laptop), one prefix per customer. SQS API with worker processes (ElasticMQ on the laptop). Gemini models through OpenRouter for scans, chat and embeddings, with RapidOCR on the CPU to pin boxes, check values and read when Gemini is down. Everything runs with Docker Compose. After handover: AWS in Mumbai (ap-south-1), with its infrastructure as code.

Folders: `backend/`, `frontend/`, `infra/`, `docs/`.

Commands for install, run, test, lint and typecheck are in `docs/running-locally.md`. If one you need is not there, find it in `backend/pyproject.toml` or `frontend/package.json` and say where you found it. Never guess one.

## Branches, pull requests and CI

Branch: `<type>/<sprint>-<short-name>`, for example `feat/s1-upload-endpoint`. Types: `feat`, `fix`, `chore`, `docs`, `test`, `infra`.

Pull request title: `feat(s1): upload endpoint with deduplication`; CI fails any other shape. Fill the template fully.

A ruleset on `main` blocks the merge until CI is green and Vrushit has approved. CI runs on pull requests into `main` and checks:

| Scope | Checks |
|---|---|
| Always (`ci.yml`) | No secret files or secret values in the diff. No conflict markers. No file over 2MB. |
| The pull request (`pr.yml`) | Title and branch format. Size, failing over 1000 changed lines. Tests alongside code, suppressed checks, new dependencies and identity-number shapes, as warnings. |
| Backend, from `backend/` | `ruff check`, `ruff format --check`, `mypy`, migrations from an empty database, then `pytest`. Dependency licences, failing on AGPL, SSPL or GPL v3. |
| Frontend, from `frontend/` | `npm ci`, lint, typecheck, tests, production build. |

**Customer isolation tests carry the pytest marker `isolation`** and run first, on their own, so a failure is impossible to miss. Any test proving one customer cannot reach another customer's data gets that marker.

If a hook or permission rule blocks you, do not work around it. Explain what you needed and ask.

## Done means

- [ ] Acceptance criteria met, and the plan task named
- [ ] Tests added or updated, and they fail without this change
- [ ] Customer separation and the source rule both hold
- [ ] Lint, format and typecheck pass on the changed code
- [ ] No secrets in the diff, no unapproved dependency
- [ ] Diff self reviewed: minimal, only what was asked, no debug leftovers, under 1000 changed lines
- [ ] Docs updated if behaviour or config changed

## How to report back

**Summary**, one to three lines. **Changes**, files and why. **Verification**, the commands you ran and their output, with NOT RUN items named. **Assumptions and open questions**, labelled. **Not done**, anything you noticed but deliberately did not build.
