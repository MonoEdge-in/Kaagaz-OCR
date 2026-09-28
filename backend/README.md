# Kaagaz backend

FastAPI API, the document worker, and everything between an upload and a cited
answer. Python 3.12. Anything that waits for a Vrushit decision is marked
**PROVISIONAL** in its docstring.

## What is here

| Folder | What it does |
|---|---|
| `kaagaz/api/` | FastAPI: sign-in (signed session cookie), documents, chat, review, admin, health. JSON errors, request ids, rate limit |
| `kaagaz/ingestion/` | Upload: read once (SHA-256, size limit), type from first bytes, per-customer duplicates, store, record, queue, undo on failure |
| `kaagaz/scanning/` | Virus gate: only an explicit CLEAN goes on; REJECTED is final |
| `kaagaz/jobs/` | Worker loop: one job at a time, retries through leases, failure bin |
| `kaagaz/pipeline.py` | The job: scan, page pictures, read (two readers), mask, pieces, values, labels, save, index |
| `kaagaz/preparing/` | Page sizes and page pictures for PDFs and images |
| `kaagaz/reading/` | The reader plug socket: CSV, Excel, PDF text (pdfplumber), a second PDF reader (PDFium), OCR for scans (RapidOCR) |
| `kaagaz/masking/` | Masks full Aadhaar and PAN numbers before anything is kept |
| `kaagaz/chunking/` | Pieces: one per spreadsheet row, or lines grouped on a page, each with its regions |
| `kaagaz/extraction/` | Values with page and region: spreadsheet columns, labelled invoice fields, verified or needs review, remembered corrections |
| `kaagaz/search/` | Search: exact words, typo tolerant, meaning (pgvector), fused; embedding pieces |
| `kaagaz/answering/` | Questions: Hinglish, dates, fixed queries for numbers, evidence gate, drafting, claim check, labels |
| `kaagaz/ai/` | The one client for the local model (Ollama); everything works without it |
| `kaagaz/adapters/` | S3 storage, SQS queue, clamd scanner |
| `kaagaz/db/` | Every SQL statement, always inside a transaction with the customer set |
| `migrations/` | The schema, with row level security on every customer table |
| `kaagaz/cli.py` | `create-customer`, `create-user`, `seed-demo` |

## Run it

See [../docs/running-locally.md](../docs/running-locally.md). In short, from this folder:

```
pip install -e ".[dev,ocr]"
alembic upgrade head                      # needs DATABASE_URL
uvicorn kaagaz.api.main:app --port 8000   # the API
python -m kaagaz.worker_main              # the worker
```

## Test it

```
pytest -m isolation     # customer separation, first and on its own
pytest                  # everything; tests marked db need DATABASE_URL
pytest -m "not db"      # without a database
ruff check . && ruff format --check . && mypy .
```

## Rules the code keeps

* Every query runs inside `Database.customer(...)`: the app role, the current
  customer, and row level security. Queries also name the customer.
* No full Aadhaar or PAN reaches a table, a log, or a model prompt.
* Every value and every piece carries its page and region; an answer without a
  source is not an answer.
* "Not found" and "needs review" are correct answers; nothing is guessed.
* Document text is data, never instructions to a model.
* Errors carry short codes only, never document content.
