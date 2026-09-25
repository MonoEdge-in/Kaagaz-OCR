# How we work on Kaagaz-OCR

Two people, five build days, and an AI assistant that writes code faster than anyone can review it. These rules keep that pace safe.

Tushar owns the steps marked **T**. Vrushit owns the steps marked **V**.

---

## The short version

1. No direct pushes to `main`. Everything goes through a pull request.
2. Vrushit reviews every pull request before it merges.
3. Keep pull requests small: about 300 changed lines or fewer.
4. Every pull request carries its tests.
5. Each day's work starts from that day's PRD in `docs/prd/`.

A ruleset on `main` enforces rules 1 to 3: direct pushes are rejected, a merge needs Vrushit's approval, and the four CI checks must be green. If a merge is blocked, the reason is on the pull request. Don't look for a way around it; ask.

---

## Where everything lives

| Thing | Where |
|---|---|
| The architecture and its decisions | [Document-to-SQL Pipeline](https://claude.ai/artifact/7ATLDAJRxG3Qaab16NhjN8), summarised in `docs/architecture.md` |
| The day-by-day plan | [Five-Day Laptop Build](https://claude.ai/artifact/4TmQt2EUoM784YqgWeyQUo) |
| Each day's requirements | `docs/prd/day-N-prd.*` |
| The individual tasks | GitHub issues, one per workstream, each naming its day |
| Rules for the AI assistant | [CLAUDE.md](CLAUDE.md), loaded automatically every session |
| Questions and discussion | The issue itself, not chat, so the answer can be found later |

---

# The day loop

It repeats for each of the five build days, and later for hosting.

## Stage 1. The day's PRD · V

1. Before the day starts, Vrushit adds that day's PRD to `docs/prd/`, in the same shape as Day 1's.
2. He opens one issue per workstream in the PRD. Each issue's acceptance criteria are that workstream's "Done when" items.

## Stage 2. Read it and ask · T

3. Read the whole PRD and every issue for the day before touching code.
4. Ask every question in its issue. There is no such thing as too many; a question costs minutes now and a day later.
5. If a task touches the database schema, the workflow's steps, an API contract or a new dependency, post a short plan in the issue and wait for Vrushit's approval. Claude Code's plan mode is the quickest way to write one.

## Stage 3. Build it · T

6. Branch from `main`:

    ```
    <type>/<scope>-<short-name>
    ```

    Types: `feature`, `fix`, `chore`, `docs`, `test`, `infra`. Scopes: `d1` to `d5` for the build days, `host` for AWS hosting, `repo` for repository housekeeping. For example:

    ```
    feature/d1-upload-api
    feature/d2-excel-parser
    fix/d3-handwriting-crop
    infra/host-terraform-vpc
    ```

7. **One job per branch.** A pull request that renames files and adds a feature is two pull requests. Past about 300 changed lines, stop and split it: plumbing first, behaviour next. A large pull request gets skimmed rather than reviewed, and skimming is how bugs reach `main`.
8. **Tests ship in the same change.** New behaviour gets a test that fails without it. A bug fix starts with a test that reproduces the bug.

## Stage 4. Check it yourself · T

9. Run the checks from the repository root:

    ```
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy
    uv run pytest
    ```

    If the change adds a migration, also run `uv run alembic upgrade head`, `uv run alembic downgrade base` and `uv run alembic upgrade head` against the local database.

10. Run `/pr-check` in Claude Code. It walks the branch against every rule here and reports **READY** or **FIX FIRST**.
11. Fix everything it flags, so Vrushit's review goes on judgement rather than rule breaks.

## Stage 5. Open the pull request · T

12. Open it against `main` and fill in the template completely. The acceptance criteria are not optional.
13. Put `Closes #<issue>` in the description, so the issue closes when the pull request merges.
14. Match the title to the branch: `feature(d1): upload links and completion`. CI fails any other shape, because the title becomes the commit message on `main`.
15. Wait for CI to go green. A red check blocks the merge; its log says what to fix.

## Stage 6. Review and merge · V then T

16. Vrushit reviews on the day it opens. If no review has landed by the end of the day, ping him rather than stacking another pull request on top.
17. Address the comments and push again. A new push dismisses the earlier approval, so it needs approving again.
18. Resolve every conversation; an unresolved one blocks the merge.
19. Vrushit squash-merges, so `main` keeps one commit per pull request, and the branch is deleted.

## Stage 7. End of the day · T then V

20. Send the hand-off the PRD asks for: the pull requests, a two-minute screen recording of the day's "Done when" check, and notes on anything that took longer or differs from the PRD.
21. Vrushit checks the day against its "Done when" and ticks it on the [Five-Day Laptop Build](https://claude.ai/artifact/4TmQt2EUoM784YqgWeyQUo) page. The next day starts only once that check passes.

## After Day 5

Hosting in AWS follows the same loop with the `host` scope. Infrastructure is Vrushit's area: take on only the hosting tasks he assigns.

---

## The rules that are never bent

These aren't style preferences. Breaking one is a blocking review comment every time.

- **Documents stay in our environment.** Never send document content to an outside AI service. Models run through Ollama on the laptop or vLLM on our own GPU.
- **Samples only.** Never put a real client document in the repository or on the laptop.
- **Every extracted value is traceable.** It's stored with its block, page, position, model and confidence, or it isn't stored as a fact.
- **Settings come from the environment.** Never hard-code a host, key, path or model name.
- **Never commit a secret.** No keys, tokens, passwords, connection strings or `.env` files. If one is committed by accident, tell Vrushit at once and treat it as compromised.
- **Never add a library without Vrushit's approval**, licence included. CI fails on AGPL, SSPL and GPL-3.0, and on any package with no declared licence.
- **Schema changes go through migrations.** Uploads add rows, never tables.
- **Never game a check.** No deleted or skipped tests, weakened assertions, blanket `try/except`, `# type: ignore` or `--no-verify`. If a test is wrong, say why and ask.

---

## When you're stuck

| Situation | Do this |
|---|---|
| The acceptance criteria are unclear | Ask in the issue before starting. Don't guess |
| You need a decision only Vrushit can make | Comment on the issue and move to another task. Don't choose for him |
| You want to add a library | Propose it in the issue with its exact name and licence, and wait |
| You noticed something else worth fixing | Say it in one line, open an issue for it, and carry on with your task |
| A hook or permission rule blocked Claude Code | Don't work around it. Explain what you needed and ask |
| Three attempts have failed | Stop. Report what you tried, what you learned, and the options |

---

## Decisions

When we choose between real options, Vrushit adds it to the Decisions section of `docs/architecture.md`: what we chose, what we rejected, and why.
