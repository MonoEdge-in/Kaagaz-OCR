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

1. **Upload** (API). The file is read once: SHA-256 fingerprint, the type taken from its first bytes (never from its name), then that type's own size limit: PDFs 50 MB, photos and scans 20 MB, spreadsheets 10 MB, each lowered by the customer's own limit. The browser checks the same limits first (`GET /api/upload-limits`), and counts a PDF's or TIFF's pages, so a file over 100 pages is never sent. A file this customer already uploaded is returned as a duplicate. Otherwise it is stored at `customers/{customer}/documents/{document}/original`, recorded as `received`, and a job naming (customer, document) goes on the queue. If a step fails, the earlier steps are undone, so a retry works.
2. **Scan** (worker). Only an explicit CLEAN verdict lets a file through, and `rejected` is final. A scanner that's down means a retry later, and the file is never opened meanwhile.
3. **Prepare.** Page sizes, plus a picture of each page for PDFs and photos at `.../pages/{n}.webp` (stored as WebP; the model still reads the lossless PNG). A file over `MAX_PAGES` fails `too_many_pages` with its real page count stored, so the message names it.
4. **Read** through the plug socket: CSV, Excel, PDF text (pdfplumber) with a second PDF reader (PDFium), and the OCR engine for scans. Each PDF page keeps its text layer only if it passes the route check (`TEXT_LAYER_*`, `SCAN_IMAGE_COVERAGE`; hidden text, old Hindi fonts and broken font maps go to OCR); a blank page (`BLANK_PAGE_STDDEV`) is never sent to OCR. Each page stores its route (`cells`, `text_layer`, `ocr`, `blank`) and its effective DPI, flagged under `LOW_RES_DPI`. A type with no reader is marked `needs_reader`.
5. **Pieces and values.** Text is split into pieces for search, each with its regions. A piece runs on across a page break; it starts only at a title or heading, a table, a blank-line gap or 800 characters. Running headers and footers ("Page 15 of 20") are left out of pieces but kept in the reader output. Table rows are written "Header: value | …", at most 20 rows a piece. Values come from spreadsheet columns and labelled invoice fields. Every value is labelled verified or needs review, and this customer's remembered corrections are applied.
5b. **The document card** (model on). One call to `MODEL_CHAT` with the first two pages, every heading and the last page, fenced as untrusted data and cut to `CARD_MAX_CHARS` and the tokens `MODEL_NUM_CTX` leaves. The model only quotes: each party, identifier, date and key fact is found in the reader's text, and the text at that spot is what is stored (`record_key = 'card'`, always needs review, never counted by a fixed query); a quote it can't find is dropped and counted. The card gives the document's type, languages, English summary and date, which also go in each piece's context header, and links it to this customer's other documents that share an invoice, PO, challan, survey, khata, account or reference number (never a GSTIN or PAN). With the model off or down there is no card: the document is saved exactly as without a model, and the run keeps the error code.
5c. **The invoice as a typed row** (PRD 2 E1 to E5, K1 to K3). When the card says `invoice`, the reader's text is read in windows (`EXTRACT_WINDOW_CHARS` 1700 with `EXTRACT_WINDOW_OVERLAP` 400, at most `EXTRACT_MAX_WINDOWS` 10; PROVISIONAL starting values, each window's reply within `MODEL_WINDOW_MAX_TOKENS`), one call each. The model only quotes the invoice number, date, seller and buyer with their GSTINs, subtotal, tax total and total, and each line item; each quote is found in the reader's text inside its window, and those values replace the labelled fields they cover (never both, so no total counts twice). The row (`invoices`) and its lines (`invoice_lines`) are typed from those values in the same save. With the model off (or no invoice values), the row comes from the labelled fields, with no lines. Every row is checked: the lines add up to the subtotal, subtotal plus tax (the stated tax total, else CGST + SGST + UTGST + IGST + cess) is the total, each within 1.00; the number and total are there; the date is from 2000 to tomorrow in `TIMEZONE`; each GSTIN has the right mod-36 check character. A failed check puts the values involved in review (`check_failed`). A value the model located is verified only when two readers agree, the label rules find the same value at the same spot, and the check passed; otherwise it is needs review (`model_chosen`). Line items are never used by the fixed queries. The rows are copies: a reviewer's fix changes the value, and the row follows on the next read.
6. **Save** in one transaction that replaces any earlier run, so a job delivered twice is safe. The document must still be clean at this point.
7. **Index.** A meaning vector is made for each piece if a model is on. The document is then `ready`.

Document states: `received`, `clean`, `ready`, plus `rejected` (for good), `needs_reader` and `failed`, each failure with a short code. While a document is read it shows how far it has got ("Reading page 3 of 40…"). Each delivery of a job is one `processing_runs` row with its routes, counts, seconds and set-up; only the open run may save or mark the document ready or failed (the run fence), so a stale delivery ends quietly. A reviewer or admin can "Read again" a ready, failed or unreadable document (20 an hour per customer, once in 10 minutes per document, PROVISIONAL): remembered fixes come back, other fixes are lost. The worker tries a job `JOB_MAX_ATTEMPTS` (5) times and writes each try on the document, so the list says "Try 2 of 5". The delivery after the last try marks the document `failed | retries_exhausted` ("We tried 5 times…") and buries the job; if the database is down just then, a later delivery marks it. So the queue's own `maxReceiveCount` must be above `JOB_MAX_ATTEMPTS + 1` (10 on the laptop), and the worker refuses to start otherwise, logging `queue_redrive_too_low` or `queue_has_no_failure_bin`. The hourly trash purge can't stop or crash the worker: a database error there is logged and the jobs go on. One bad file never blocks the ones behind it.

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

The review queue shows only values of each family's current, ready version, out of Trash: an older version's values no longer reach any answer. A reviewer picks the first reading, the second, or types the value. The fix is logged in `corrections`. If they choose to remember it, it goes into `learned_corrections` and is applied to this customer's later documents. The original file never changes.

## Data and customer separation

Tables: `customers`, `users`, `documents`, `pages`, `reader_outputs`, `pieces`, `extracted_values`, `corrections`, `learned_corrections`, `conversations`, `messages`, `feedback`, `audit_log`, `folders`, `tags`, `document_tags`, `invoices`, `invoice_lines`, `document_links`, `processing_runs`. Every one of them except `customers` carries the customer. A document's invoice, lines and links are rebuilt by each run of the pipeline; `processing_runs` keeps one row per run, and outlives its document (PROVISIONAL).

Customers are kept apart by two locks:

1. **The database lock.** Row level security is on every customer table. The app connects through `kaagaz_app`, a role that isn't a superuser, doesn't own the tables and can't bypass the rules. Every transaction sets the current customer first.
2. **The application lock.** Every query also names the customer. One function lists across customers: `kaagaz_customer_ids_with_trash` returns only the ids of customers with trash old enough to purge, for the worker's hourly purge (PROVISIONAL).

The `isolation` tests prove both on every pull request.

**Folders, versions and Trash.** Folders and tags belong to a document's whole family of versions. The list, search, chat, review and the admin numbers use each family's current version: the highest version that finished reading. A newer version still reading, or failed, is shown beside it and never hides it. Trash means deleted: a family in Trash can't be renamed, moved, tagged, downloaded or given a new version, only restored or deleted forever. Moving to Trash and restoring act on every version, so only an admin or the user who uploaded every version may do them. Deleting forever, and deleting a tag, need an admin session whose admin has two-step sign-in on. Saved chat answers hide the words and highlights of any source that is now in Trash or deleted, and mark a source that a newer version replaced.

## Security

| Threat | Control |
|---|---|
| One customer reaching another's data | Both locks above; a job naming another customer's document stops before anything is read |
| A malicious file | Virus scan first, deny by default; caps on sizes, cells, pages and pixels; no macros; nothing external |
| Leaks through logs or errors | Errors and logs carry ids and short codes only, never file content or file names |
| Identity numbers | Not masked: kept and shown as read (see Decisions). Logs and errors still carry ids and codes only |
| Document text steering the AI | Text is quoted as data; links and markup are stripped from answers |
| Forged requests | A signed session cookie, `X-Requested-With: kaagaz` on every change, and a rate limit |
| Guessing passwords and codes | 5 wrong passwords lock an account for 15 minutes. Two-step sign-in (a 6-digit code) is each admin's choice, turned on or off in Admin, Settings with the current password; deleting forever and deleting a tag need it on. The admin screen never checks a non-admin's password, and a used enrol token is refused without counting, so neither can lock anyone out. A password change, a role change, a disabled account or a 2FA reset ends every session |
| Migrations blocking the app | Every migration runs with `lock_timeout` 10 s (`migrations/env.py`), so a busy table makes it fail fast instead of queueing requests |
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
| The design is this chat product plus the pipeline upgrades in PRDs 1 to 3 (29 Sep 2026). The NL-to-SQL agent and AWS hosting come after handover | Settles the chat product versus Document-to-SQL question: the pipeline's typed tables and reader are built into this product |
| Documents in English, Hindi and Marathi; answers in English only (29 Sep 2026) | Customers' papers include Hindi and Marathi; one answer language keeps checking simple |
| Scans are read by PaddleOCR-VL, in its own container; RapidOCR stays as the fallback engine (`OCR_ENGINE`) | Reads the three languages with layout and tables; the fallback runs on weak laptops |
| The second reading of a scanned value is Qwen vision on that value's cropped box | Independent of the OCR engine, and only as costly as the number of values |
| Models: Qwen3.5 4B for chat, extraction and vision; Qwen3-Embedding-0.6B for meaning vectors, all through Ollama | Fits a 16 GB laptop next to the OCR model; 9B is a setting change for a demo machine |
| The queue worker stays; Temporal isn't used | Retries, leases and a failure bin already work; Temporal pays off only for hosted workflows that wait days |
| Vectors live in the same PostgreSQL (pgvector) | One place for customer separation, filters and backups; revisit past a few million pieces |
| A job is tried 5 times (`JOB_MAX_ATTEMPTS`), and each try shows in the file list (29 Sep 2026) | The queue's own limit (10) leaves spare deliveries to mark a document failed even if the database is down |
| When two versions of a document finish reading, the highest version number is current (29 Sep 2026) | An older upload finishing late never replaces a newer one |
| Trash means deleted: a document there can't be renamed, moved, tagged, downloaded or given a new version (29 Sep 2026) | Only Restore or Delete forever |
| Admin two-step sign-in is each admin's choice, turned on or off in Admin, Settings with the current password (30 Sep 2026) | Replaces the rule that every admin must enrol. Turning it on shows a QR code, made by our own encoder (no new library) |
| Deleting forever and deleting a tag need an admin session whose admin has two-step sign-in on (30 Sep 2026) | Both can't be undone, so they must never rest on a password alone |
| A PDF or TIFF over 100 pages is refused in the browser before upload, after counting its pages (29 Sep 2026) | Nothing is uploaded that can't be read; the worker still checks |
| Dependencies approved in the PRDs: `cryptography` (PRD 1); `paddleocr`, `paddlepaddle` (in the OCR image only) and `indic-transliteration` (PRD 2) | Everything else still needs approval |
| The time per page and the route thresholds are measured with `python -m kaagaz.cli measure` on the reference laptop (PRD 2 X4). Seconds per page by route and file type: NOT RUN yet, so the upload estimate and `.env.example` keep their starting values | No number is written here until it is measured |

**Open**

| Decision | Blocks |
|---|---|
| What "verified" promises, in writing | The labels users see |
| The virus scanner and upload limits in AWS (values marked PROVISIONAL in `.env.example`) | Hosting |
| The go or no-go bar for launch, as a number | Launch |

Not built yet, against the Document-to-SQL plan: typed tables beyond invoices (land records, bank statements), and a search tool outside chat. The typed `invoices` and `invoice_lines` tables and their checks came in PRD 2 (step 5c).
