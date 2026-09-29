# Kaagaz architecture and decisions

The one file for the system design and every decision. Keep it current: when the design or a decision changes, update this file in the same pull request. Don't add another design or decision file beside it.

## The product

Customers upload business documents (invoices, registers, receipts, statements) and ask questions about them in chat, including Hinglish questions. Every answer points to its source: the document, the page and the exact spot. If the documents don't hold the answer, the system says so. Several customer companies share one system, and none of them ever sees another's data. At launch it reads English documents only.

## The rules the design is built around

1. **Customers never cross.** Every query runs with the current customer set, and the database refuses anything else.
2. **No source, no answer.** Every value and every piece of text carries its document, page and position.
3. **Verified or needs review, never guessed.** "Unreadable" and "not found" are valid answers.
4. **Readers sit behind one plug socket.** Swapping the reader for a file type touches one module.

## The parts

| Part | On the laptop | In AWS (Mumbai) |
|---|---|---|
| Web app: Next.js with PDF.js | `frontend` container, port 3000 | the same image |
| API: FastAPI, under `/api/*` | `api` container, port 8000 | the same image |
| Worker: one job at a time | `worker` container | the same image |
| Database: PostgreSQL 17 with pgvector and pg_trgm | `postgres` | Amazon RDS |
| File storage, through the S3 API | SeaweedFS | Amazon S3 |
| Job queue, through the SQS API, with a failure bin | ElasticMQ | Amazon SQS |
| Virus scanner | ClamAV | ClamAV (provisional) |
| Local AI: chat, embeddings and an optional vision reader | Ollama; everything works without it | provisional |

The browser talks only to the web app, which forwards `/api/*` to the API, so the session cookie stays first-party.

## One upload, step by step

1. **Upload** (API). The file is read once: SHA-256 fingerprint, size limit, and the type taken from its first bytes, never from its name. A file this customer already uploaded is returned as a duplicate. Otherwise it is stored at `customers/{customer}/documents/{document}/original`, recorded as `received`, and a job naming (customer, document) goes on the queue. If a step fails, the earlier steps are undone, so a retry works.
2. **Scan** (worker). Only an explicit CLEAN verdict lets a file through, and `rejected` is final. A scanner that's down means a retry later, and the file is never opened meanwhile.
3. **Prepare.** Page sizes, plus a picture of each page for PDFs and photos at `.../pages/{n}.webp` (stored as WebP; the model still reads the lossless PNG).
4. **Read** through the plug socket: CSV, Excel, PDF text (pdfplumber) with a second PDF reader (PDFium), and RapidOCR for scans. A type with no reader is marked `needs_reader`.
5. **Pieces and values.** Text is split into pieces for search, each with its regions. Values come from spreadsheet columns and labelled invoice fields. Every value is labelled verified or needs review, and this customer's remembered corrections are applied.
6. **Save** in one transaction that replaces any earlier run, so a job delivered twice is safe. The document must still be clean at this point.
7. **Index.** A meaning vector is made for each piece if a model is on. The document is then `ready`.

Document states: `received`, `clean`, `ready`, plus `rejected` (for good), `needs_reader` and `failed`, each failure with a short code. A job that keeps failing goes to the failure bin on the queue's last delivery, and its document ends `failed | retries_exhausted`; the worker reads that maximum from the queue's redrive policy at start-up and will not start without one. One bad file never blocks the ones behind it.

## One question, step by step

1. Hinglish words and dates ("last month", in `TIMEZONE`) are understood.
2. A question asking for a number or a list runs a fixed, tested query, never SQL written by an AI.
3. Anything else searches this customer's pieces three ways: exact words (full-text), typo tolerant (trigram) and meaning (pgvector). The three rankings are merged into one.
4. If the evidence isn't strong enough, the answer is "I couldn't find this in your documents."
5. The answer is drafted from the evidence only, with document text treated as data, never as instructions. Any claim without a piece and position is dropped, and each answer is labelled verified or needs review.

## Values, review and corrections

Every value keeps its raw reading, the second reading if there is one, its page, its region, the reader and a confidence score. Labels and their reasons:

| Label | Reason |
|---|---|
| verified | `direct_cell` (a spreadsheet cell), `readers_agree`, `corrected`, `learned_correction` |
| needs review | `readers_disagree`, `single_reader`, `low_confidence` |

A reviewer picks the first reading, the second, or types the value. The fix is logged in `corrections`. If they choose to remember it, it goes into `learned_corrections` and is applied to this customer's later documents. The original file never changes.

## Data and customer separation

Tables: `customers`, `users`, `documents`, `pages`, `reader_outputs`, `pieces`, `extracted_values`, `corrections`, `learned_corrections`, `conversations`, `messages`, `feedback`, `audit_log`, `folders`, `tags`, `document_tags`. Every one of them except `customers` carries the customer.

Customers are kept apart by two locks:

1. **The database lock.** Row level security is on every customer table. The app connects through `kaagaz_app`, a role that isn't a superuser, doesn't own the tables and can't bypass the rules. Every transaction sets the current customer first.
2. **The application lock.** Every query also names the customer. One function lists across customers: `kaagaz_customer_ids_with_trash` returns only the ids of customers with trash old enough to purge, for the worker's hourly purge (PROVISIONAL).

The `isolation` tests prove both on every pull request.

## Security

| Threat | Control |
|---|---|
| One customer reaching another's data | Both locks above; a job naming another customer's document stops before anything is read |
| A malicious file | Virus scan first, deny by default; caps on sizes, cells, pages and pixels; no macros; nothing external |
| Leaks through logs or errors | Errors and logs carry ids and short codes only, never file content or file names |
| Identity numbers | Not masked: kept and shown as read (see Decisions). Logs and errors still carry ids and codes only |
| Document text steering the AI | Text is quoted as data; links and markup are stripped from answers |
| Forged requests | A signed session cookie, `X-Requested-With: kaagaz` on every change, and a rate limit |
| Secrets and dependencies | No secrets in git, and CI checks for them; every new library needs approval; AGPL, SSPL and GPL v3 are refused |

## Code map

| Folder | What |
|---|---|
| `backend/kaagaz/api/` | Sign-in, documents, chat, review, admin, health |
| `backend/kaagaz/ingestion/`, `scanning/`, `jobs/`, `pipeline.py` | Upload, the virus gate, the worker loop, and one job end to end |
| `backend/kaagaz/preparing/`, `reading/` | Page pictures, and the reader plug socket with its readers |
| `backend/kaagaz/chunking/`, `extraction/` | Pieces, and values with their labels |
| `backend/kaagaz/search/`, `answering/`, `ai/` | Search, answers, and the one client for the local model |
| `backend/kaagaz/adapters/`, `db/`, `migrations/` | S3, SQS and clamd; every SQL statement; the schema |
| `frontend/lib/`, `app/`, `components/` | Every backend call, the screens, and their parts |

**Adding a reader.**
- A reader has one method, `read(data) -> spans`, where each span has a non-empty cleaned `text`, a `raw` value and a `location`.
- It raises `ReadError("short_code")` for a damaged file, and `NoReader(type)` for a type it doesn't handle.
- It never stores, logs or sends anything; the pipeline does that.
- To register it, add one entry to the socket's mapping, keyed by the type in `kaagaz/ingestion/sniff.py`.
- Any library it needs is a new dependency, which needs approval first.

**Frontend rules.**
- Document text and server messages are shown as plain text, never as HTML.
- Hiding a button is only a hint: the server checks the role on every request.
- Colours come only from the tokens in `app/globals.css`.

## Decisions

**Made**

| Decision | Why |
|---|---|
| Several customers in one database, kept apart by row level security plus a customer filter in every query | Cheaper than a database per customer, and still safe |
| SQS-style queue with a failure bin and at-least-once delivery; jobs are safe to run twice | Simple, scales by adding workers, and one bad file never blocks the rest |
| SeaweedFS on the laptop (MinIO is AGPL) and S3 in AWS, through the same code | One storage client everywhere |
| Numbers only come from fixed, tested queries | AI-written SQL can be confidently wrong |
| No masking of Aadhaar or PAN numbers: they're kept and shown exactly as read (decided 28 Sep 2026) | Replaces the earlier mask-at-capture rule; the masking code is removed |
| Everything works with the AI off (`MODEL_ENABLED=false`) | The laptop and CI run without models; a model that's down only loses meaning search |

**Open**

| Decision | Blocks |
|---|---|
| Which design is current: this chat product, or the Document-to-SQL pipeline plan (Temporal, PaddleOCR, typed invoice tables, a search tool for an NL-to-SQL agent) | Which gaps get built next |
| The AI that reads scans, and the second independent reader (today RapidOCR, and an optional `MODEL_VISION`) | Scan and handwriting quality |
| What "verified" promises, in writing | The labels users see |
| The chat and embedding models (`MODEL_CHAT`, `MODEL_EMBED`) | Answer quality and speed |
| The virus scanner and upload limits in AWS (values marked PROVISIONAL in `.env.example`) | Hosting |
| The go or no-go bar for launch, as a number | Launch |

Not built yet, against the Document-to-SQL plan: folders, tags and versions; the typed `invoices` and `invoice_lines` tables and the document-type step; rule checks such as whether the totals add up; and a search tool outside chat.
