## What this changes

<!-- One or two sentences: what it does, and why now. -->

Closes #

**Day and PRD section:** <!-- for example: Day 1, section 5 (FastAPI app) -->

## Acceptance criteria

<!-- Copy the "Done when" items from the issue. If the issue has none, it wasn't ready. -->

- [ ]
- [ ]

## How I tested it

<!-- The commands you actually ran and what you saw. Not "it works". -->

## Checklist

- [ ] Under about 300 changed lines, and does one job
- [ ] Tests included, and they fail without this change
- [ ] `ruff check`, `ruff format --check`, `mypy` and `pytest` pass locally
- [ ] Any migration applies, rolls back and applies again on an empty database
- [ ] No new dependency, or Vrushit approved it (name it here, with its licence)
- [ ] No secrets, keys or `.env` files, and no real documents
- [ ] No hard-coded hosts, keys, paths or model names: everything comes from settings
- [ ] Every extracted value is stored with its block, page, position, model and confidence
- [ ] Workflow code does no I/O, and only IDs and small values pass through Temporal
- [ ] No document content sent to an outside service
- [ ] Docs updated if behaviour, settings or the schema changed (including the data dictionary)

## What the reviewer should look at closely

<!-- Where you're unsure, where you made a judgement call, and what you left out on purpose. -->
