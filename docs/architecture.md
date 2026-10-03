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
| Database: PostgreSQL 17 with pgvector 0.8 and pg_trgm | `postgres` | Amazon RDS |
| File storage, through the S3 API | SeaweedFS | Amazon S3 |
| Job queue, through the SQS API, with a failure bin | ElasticMQ | Amazon SQS |
| Virus scanner | ClamAV | ClamAV (provisional) |
| Local AI: chat, embeddings and an optional vision reader | Ollama; everything works without it | provisional |

The browser talks only to the web app, which forwards `/api/*` to the API, so the session cookie stays first-party.

**Search, in simple terms** (PRD 3, `search/hybrid.py`). Three lists of at most 50 pieces each, merged by reciprocal rank fusion (k = 60), so a piece found two ways ranks above one found one way:
1. **Words:** full text on `tsv` (english) and `tsv_simple` (the text and its Latin spelling, unstemmed), plus the Marathi and Hindi equivalents of an English question's words from a fixed glossary (`answering/glossary.py`) on `tsv_simple`, plus the sound keys of the question's words (`chunking/script.py::sound_key`, so "bhogwatdar" finds भोगवटादार) on `tsv_sound` (migration 0011: `kaagaz_sound_key_v1(coalesce(text_latin, text))`, stored); a piece scores the highest, and pieces an exact query matched come first. Marathi and Hindi typed in English letters reuse the glossary through its Romanised spellings (malak is owner), and their fillers (ahe, kiti) are left out of the keywords. An answer only the glossary or a sound match let through is needs review (PROVISIONAL, 2 Oct 2026; sound keys and Romanised words 3 Oct 2026).
2. **Typos:** trigram `word_similarity` on `text` and on `text_latin`, the higher of the two, so "shindey" finds "शिंदे".
3. **Meaning:** each piece's vector is a `halfvec(1024)` (half precision, 2 KB) on the HNSW index `pieces_embedding` (m 16, ef_construction 64), walked with pgvector's iterative scan and `SEARCH_EF`. If the walk comes back with fewer than 50 rows, the same filters run as an exact scan.

One `Scope` (`search/scope.py`) narrows all three lists before ranking: a folder (its subfolders only when asked), tags (all of them), document types, up to 50 documents, and dates (the document's date, else its upload day in `TIMEZONE`, both ends included). `GET /api/search` returns the best documents, at most `SEARCH_MAX_DOCUMENTS`: the Documents screen's "Inside documents" mode, beside "Names".

**Also from PRD 3.** The document screen shows the card, related documents (both directions, only to ready, current documents out of Trash, at most 50 links), the invoice with its check chip, and a warning on a page under `LOW_RES_DPI`. The review queue filters by reason (`?reason=`). Admin has a Failed jobs tab (`GET /api/admin/failed`: current versions that are `failed` or `needs_reader`), whose Retry is the Read again route. Chat shows which documents the next question uses (a chip and a picker: all documents, one folder, or the document the viewer's "Ask about this document" opened it for), "Searching for: …" for a rewritten follow-up, and "Pages 15 to 16" or "Label on page 15, value on page 16" on a source. `python -m kaagaz.cli reembed` makes the vectors again after a model or recipe change. Migration 0007 is the data dictionary: a comment on every table and column, which the NL-to-SQL agent will read from `pg_description`. The golden questions (`backend/tests/golden/`, 30, frozen by sha256) and their live runner `backend/scripts/run_golden.py` measure the answers; CI tests the scorer, never a live run.

## One upload, step by step

1. **Upload** (API). The file is read once: SHA-256 fingerprint, the type taken from its first bytes (never from its name), then that type's own size limit: PDFs 50 MB, photos and scans 20 MB, spreadsheets 10 MB, each lowered by the customer's own limit. The browser checks the same limits first (`GET /api/upload-limits`), and counts a PDF's or TIFF's pages, so a file over 100 pages is never sent. A file this customer already uploaded is returned as a duplicate. Otherwise it is stored at `customers/{customer}/documents/{document}/original`, recorded as `received`, and a job naming (customer, document) goes on the queue. If a step fails, the earlier steps are undone, so a retry works.
2. **Scan** (worker). Only an explicit CLEAN verdict lets a file through, and `rejected` is final. A scanner that's down means a retry later, and the file is never opened meanwhile.
3. **Prepare.** Page sizes, plus a picture of each page for PDFs and photos at `.../pages/{n}.webp` (stored as WebP; the model still reads the lossless PNG). A file over `MAX_PAGES` fails `too_many_pages` with its real page count stored, so the message names it.
4. **Read** through the plug socket: CSV, Excel, PDF text (pdfplumber) with a second PDF reader (PDFium), and the OCR engine for scans. Each PDF page keeps its text layer only if it passes the route check (`TEXT_LAYER_*`, `SCAN_IMAGE_COVERAGE`; hidden text, old Hindi fonts and broken font maps go to OCR); a blank page (`BLANK_PAGE_STDDEV`) is never sent to OCR. Each page stores its route (`cells`, `text_layer`, `ocr`, `blank`) and its effective DPI, flagged under `LOW_RES_DPI`. A type with no reader is marked `needs_reader`.
5. **Pieces and values.** Text is split into pieces for search, each with its regions. A piece runs on across a page break; it starts only at a title or heading, a table, a blank-line gap or 800 characters. Running headers and footers ("Page 15 of 20") are left out of pieces but kept in the reader output. Table rows are written "Header: value | …", at most 20 rows a piece. Values come from spreadsheet columns and labelled invoice fields. Every value is labelled verified or needs review, and this customer's remembered corrections are applied.
5b. **The document card** (model on). One call to `MODEL_CHAT` with the first two pages, every heading and the last page, fenced as untrusted data and cut to `CARD_MAX_CHARS` and the tokens `MODEL_NUM_CTX` leaves. The model only quotes: each party, identifier, date and key fact is found in the reader's text, and the text at that spot is what is stored (`record_key = 'card'`, always needs review, never counted by a fixed query); a quote it can't find is dropped and counted. The card gives the document's type, languages, English summary and date, which also go in each piece's context header, and links it to this customer's other documents that share an invoice, PO, challan, survey, khata, account or reference number (never a GSTIN or PAN). With the model off or down there is no card: the document is saved exactly as without a model, and the run keeps the error code. If the card works but an invoice window then fails, the card is kept and the invoice falls back to its model-off row (step 5c).
5c. **The invoice as a typed row** (PRD 2 E1 to E5, K1 to K3). When the card says `invoice`, the reader's text is read in windows (`EXTRACT_WINDOW_CHARS` 1700 with `EXTRACT_WINDOW_OVERLAP` 400, at most `EXTRACT_MAX_WINDOWS` 10; PROVISIONAL starting values, each window's reply within `MODEL_WINDOW_MAX_TOKENS`), one call each. The model only quotes the invoice number, date, seller and buyer with their GSTINs, subtotal, tax total and total, and each line item; each quote is found in the reader's text inside its window, and those values replace the labelled fields they cover (never both, so no total counts twice). The row (`invoices`) and its lines (`invoice_lines`) are typed from those values in the same save. With the model off (or no invoice values), the row comes from the labelled fields, with no lines. Every row is checked: the lines add up to the subtotal, subtotal plus tax (the stated tax total, else CGST + SGST + UTGST + IGST + cess) is the total, each within 1.00; the number and total are there; the date is from 2000 to tomorrow in `TIMEZONE`; each GSTIN has the right mod-36 check character. A failed check puts the values involved in review (`check_failed`). A value the model located is verified only when two readers agree, the label rules find the same value at the same spot (when they find that field at all), and the check passed; otherwise it is needs review (`model_chosen`). A reviewer's remembered fix stays verified, but only when the check passed. On an invoice the model read, a failed check leaves none of its header values verified. If an invoice window fails, every window's values are dropped and the row comes from the labelled fields, as with the model off. Line items are never used by the fixed queries. The rows are copies: a reviewer's fix changes the value, and the row follows on the next read.
6. **Save** in one transaction that replaces any earlier run, so a job delivered twice is safe. The document must still be clean at this point.
7. **Index.** With a model on, `MODEL_EMBED` makes each piece's meaning vector from its reduced header (only the Type, Date and Section parts of its context header: never "Before:", the name or the pages), a blank line, then its text, with no instruction (`search/indexing.py`). Each vector is tagged `MODEL_EMBED#ctx1` in `embedding_model`, and meaning search uses only the current tag, so after a model or recipe change the old vectors are left out until `reembed` replaces them. A rename keeps the vectors (the name is never embedded). With the model down, the pieces are saved without vectors and words and typos still find them. The document is then `ready`, and the worker gives those pieces their vectors later, at most 64 every 10 minutes (waiting up to an hour while the model stays down), customer by customer under row level security.

Document states: `received`, `clean`, `ready`, plus `rejected` (for good), `needs_reader` and `failed`, each failure with a short code. While a document is read it shows how far it has got ("Reading page 3 of 40…"). Each delivery of a job is one `processing_runs` row with its routes, counts, seconds and set-up; only the open run may save or mark the document ready or failed (the run fence), so a stale delivery ends quietly. A reviewer or admin can "Read again" a ready, failed or unreadable document (20 an hour per customer, once in 10 minutes per document, PROVISIONAL): remembered fixes come back, other fixes are lost. The worker tries a job `JOB_MAX_ATTEMPTS` (5) times and writes each try on the document, so the list says "Try 2 of 5". The delivery after the last try marks the document `failed | retries_exhausted` ("We tried 5 times…") and buries the job; if the database is down just then, a later delivery marks it. So the queue's own `maxReceiveCount` must be above `JOB_MAX_ATTEMPTS + 1` (10 on the laptop), and the worker refuses to start otherwise, logging `queue_redrive_too_low` or `queue_has_no_failure_bin`. The hourly trash purge can't stop or crash the worker: a database error there is logged and the jobs go on. One bad file never blocks the ones behind it.

## One question, step by step

1. **Scope** (PRD 3 H2). A question can carry a `Scope` (`POST /api/chat`, `scope`). Without one, a follow-up keeps the conversation's last saved scope; `{}` widens it to everything. The scope is parsed and resolved again for this customer on every turn, and one that matches nothing is not found before any SQL or model call.
2. **Follow-ups** (H1). With the model on and earlier turns in the conversation, one call rewrites the question to stand on its own, from the last `CHAT_HISTORY_TURNS` question and answer pairs, fenced as data and cut to 2,000 characters (a not-found answer gives only its question). A guard accepts the rewrite only when every word in it comes from the question, the history or a short list of function words (a number or a name only from an earlier question, never from an answer), and it keeps every number, topic word and date word of the question. Otherwise, or when the model is down, the reply is the wrong shape, over 300 characters or reads like instructions, the question as asked is used, and only a reason code is logged, never the text. The rewrite is saved on the question (`messages.rewritten_question`), shown as "Searching for: …", and every step below runs on it.
3. Hinglish words and dates ("last month", in `TIMEZONE`) are understood. A question in Devanagari is first spelt in Latin letters, with its digits folded, so "पाटील का बिल" searches "patil invoice".
4. A question asking for a number or a list runs a fixed, tested query inside the scope, never SQL written by an AI. The lookup of a figure only a card holds, and the closest documents, use the scope too.
5. Anything else searches this customer's pieces inside the scope, three ways (Search, in simple terms, above). The question is embedded as `Instruct: Given a question about business documents, retrieve passages that answer it\nQuery:{question}` (the Qwen3-Embedding model card's form): the English form of a Hinglish question, a Devanagari question as asked. The search transaction sets a 3 s statement timeout, `hnsw.ef_search` and `plan_cache_mode = force_custom_plan` (a prepared statement's generic plan would skip the index), all ending with the transaction. A meaning list that times out is dropped and words and typos still answer; a word or typo list that times out fails the request with 503 `backend_unavailable`.
6. If the evidence isn't strong enough, the answer is "I couldn't find this in your documents."
7. **Neighbours** (Q4). Only after that gate, one statement fetches the pieces next to each usable hit (`seq` ± `ANSWER_NEIGHBOURS`, the same live current document, inside the scope), checked as hits are. They come after every hit, and only while they fit the room the hits leave in `MODEL_NUM_CTX`, so they are dropped first.
8. The answer is drafted from the evidence only, with document text treated as data, never as instructions. Each piece comes with its escaped context header on a `Context:` line, a neighbour is marked as one, and the model is told to answer in English, keeping a quote in its own script. Any claim without a piece and position is dropped, and each answer is labelled verified or needs review. The check reads a piece's text only, never its header. A sentence backed only by a neighbour, or holding a number only a neighbour holds, is needs review, and so is anything from an invoice whose check failed, on both routes. Each citation carries `page_from` and `page_to` with a spot on each page, and a value's label page and spot (`label_page_no`, `label_regions`).

**Search inside documents** (`GET /api/search`, PRD 3 Q2). `q` is 1 to 200 characters and never logged; the `Scope` comes from the query string, plus the file type. The scope is resolved in its own short transaction, and one that matches nothing (an empty list, an unknown folder or tag) returns no documents before the model or any search SQL runs. Then the question is embedded (outside any transaction; with the model off or down, words and typos still answer) and the three lists run as above. One pass groups the candidates by document: a document's score is its best piece's, and `more` counts its other pieces among the candidates. Each result has that piece's snippet (`clean_snippet`), pages and regions.

**Story of this PDF through the API** (`GET`, `PUT`, `DELETE /api/documents/{id}/my-story`, PROVISIONAL). The signed-in user's own story, under `Database.user`: a document of another customer, or a family in Trash, is 404, and PUT and DELETE lock the live family in the same transaction. After the save commits, with the model on, `MODEL_EMBED` embeds the story's text as it is, tagged `MODEL_EMBED#story1` (`search/story_vectors.py`). A model that is off, down or slow (`MODEL_TIMEOUT_SECONDS`) never fails the save: the story then has no vector yet, and the worker embeds it later: at most 32 every 10 minutes, waiting up to an hour while the model is down (`StoryVectors`), through `kaagaz_stories_to_embed` (0010, ids only, in key order after a cursor, so a refused vector never blocks the rest), then as each story's own user. `reembed` also embeds each customer's stories with no current vector. The story is never logged; audit rows hold ids only. **Story matches** (S3, PROVISIONAL): `GET /api/search` lists `story_matches` beside the documents, and the chat calls `GET /api/me/story-matches?q=` beside `POST /api/chat`, never through it: at most three of the user's own documents (current, ready, out of Trash, in the scope) whose story the question tells again (`search/story_match.py`: the share of the question's words found by the same word, trigram, sound key or digits; meaning OFF until calibrated), each with a band, "match" or "maybe", never a score or the story. No answer, message, prompt or gate ever sees a story.

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
2. **The application lock.** Every query also names the customer. Two functions list across customers, both SECURITY DEFINER with an empty `search_path`, returning ids only: `kaagaz_customer_ids_with_trash`, the customers with trash old enough to purge, for the worker's hourly purge, and `kaagaz_customer_ids()` (0006), every customer, for `reembed` without `--customer-id` and the worker's missing-vector pass, which then work on each customer inside that customer's own transactions (both PROVISIONAL). A third, `kaagaz_stories_to_embed` (0010), gives (customer, user, family) of stories with no vector, for the worker and `reembed`, which then read and write each story as its own user.

The `isolation` tests prove both on every pull request.

**My words** (0008, PROVISIONAL) belong to one user, not the customer: `user_words` and `user_word_spots` also have a RESTRICTIVE `own_user_only` policy on `kaagaz.user_id`, which only `Database.user` sets, so any other transaction (another user, an admin, `Database.customer`) sees and writes none of them. The routes are `/api/me/words` (list, add, change, delete; the same word with a second meaning is 409 `word_has_meaning` until confirmed) and `GET /api/documents/{id}/my-words?page=` (the caller's own spots on a page). In chat (A3, PROVISIONAL), the asker's own words the question uses (`answering/expansion.py`, read inside `Database.user`, at most 10 per question) are searched as extra OR words only through a safe builder (letter and mark runs, never the text), count at the gate only next to a keyword found as written, and make the answer needs review with an `understood_words` note; they never reach the fixed queries, the model's prompt, the sentences or the history.

**Story of this PDF** (0009, PROVISIONAL) is one user's own note about a document, not the customer's: besides `customer_only`, `document_stories` has a RESTRICTIVE `own_user_only` policy on `kaagaz.user_id`, which only `Database.user` sets, so only its author's transactions see or write it. It is keyed on the family (the first version's id), so every version shares it and a "Read again" never touches it, and it goes with the family and the user (CASCADE). An edit clears its vector in the same statement, and a vector is written only while the story still has the hash of the text that was embedded, so a late embed never stores a vector of older text. A story is what the user said, never evidence: it feeds no answer, gate, label or prompt. It has no vector or word index yet: a user's own stories are few.

**PRD 3** adds no table: 0006 adds columns and indexes to `pieces` and `messages`, and 0007 only comments. Every new statement (the three lists and the exact fallback, `/api/search`, `links`, the review filter, the failed list, `reembed`) names the customer and runs under row level security. A scope's folder and tags are looked up as this customer's, and every list keeps its customer filter, so another customer's folder, tag or document ids match nothing. The failed list needs an admin session: an app session, even an admin's, gets 403. In chat the scope is resolved again for the current customer on every turn, saved or sent, so another customer's ids match nothing, and a saved scope that no longer parses matches nothing (it is never widened). Neighbours come from the same customer's live current pieces inside the scope.

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
| Scans are read by PaddleOCR-VL, in its own container, on a GPU or demo machine; laptops keep `OCR_ENGINE=rapidocr` (30 Sep 2026) | PaddleOCR-VL reads the three languages with layout and tables, but took 3 to 11 minutes a page on a laptop CPU. Hindi and Marathi scans need it, so on a laptop they read poorly until a GPU is used |
| The second reading of a scanned value is Qwen vision on that value's cropped box | Independent of the OCR engine, and only as costly as the number of values |
| Models: `qwen3.5:4b` for chat, extraction and vision (`MODEL_CHAT`, `MODEL_VISION`); `qwen3-embedding:0.6b` for meaning vectors, all through Ollama 0.17.7 or later (compose runs 0.34.4) | Fits a 16 GB laptop next to the OCR model; one model reads text and images; 9B is a setting change for a demo machine |
| The queue worker stays; Temporal isn't used | Retries, leases and a failure bin already work; Temporal pays off only for hosted workflows that wait days |
| Vectors live in the same PostgreSQL (pgvector) | One place for customer separation, filters and backups; revisit past a few million pieces |
| A job is tried 5 times (`JOB_MAX_ATTEMPTS`), and each try shows in the file list (29 Sep 2026) | The queue's own limit (10) leaves spare deliveries to mark a document failed even if the database is down |
| When two versions of a document finish reading, the highest version number is current (29 Sep 2026) | An older upload finishing late never replaces a newer one |
| Trash means deleted: a document there can't be renamed, moved, tagged, downloaded or given a new version (29 Sep 2026) | Only Restore or Delete forever |
| Admin two-step sign-in is each admin's choice, turned on or off in Admin, Settings with the current password (30 Sep 2026) | Replaces the rule that every admin must enrol. Turning it on shows a QR code, made by our own encoder (no new library) |
| Deleting forever and deleting a tag need an admin session whose admin has two-step sign-in on (30 Sep 2026) | Both can't be undone, so they must never rest on a password alone |
| A PDF or TIFF over 100 pages is refused in the browser before upload, after counting its pages (29 Sep 2026) | Nothing is uploaded that can't be read; the worker still checks |
| Invoice windows follow the model: `EXTRACT_WINDOW_CHARS` and `EXTRACT_WINDOW_OVERLAP` left empty are sized from `MODEL_WINDOW_MAX_TOKENS` (2048 gives 1700 and 400) (30 Sep 2026) | A bigger model is one setting, not a table of model names that goes out of date |
| OCR: at most 5 minutes a page (`OCR_TIMEOUT_SECONDS=300`); a job's lease is 20 minutes (30 Sep 2026) | A stuck page fails fast enough for the worker to retry within the lease |
| Card values are always needs review and never counted by the fixed queries. A figure only a card holds gets no number: the answer asks for it to be confirmed in Review and links the documents where it appears (30 Sep 2026) | A model-found number is never given as fact before a person checks it |
| Read again: at most 20 an hour per customer, and 10 minutes between reads of one document (30 Sep 2026) | Reading is the costly step |
| Database access for the PRD 2 tables is narrower than the PRD asked (only the writes the code makes), and a document's processing runs are deleted with it (30 Sep 2026) | When a document is deleted, everything about it is forgotten; the audit log keeps only ids |
| Latin spellings use the adjusted rules in `chunking/script.py`, not the PRD's literal recipe (30 Sep 2026) | The literal recipe gives "simde" for शिंदे; search (PRD 3) matches spellings fuzzily |
| Dependencies approved in the PRDs: `cryptography` (PRD 1); `paddleocr`, `paddlepaddle` and PaddleOCR's own `paddlex` (in the OCR image only, pinned by `constraints-ocr.txt`) and `indic-transliteration` (PRD 2), with what it brings in pinned by `constraints.txt` in the image and CI, and a mypy exception because it ships no types (30 Sep 2026) | Everything else still needs approval. The OCR model files are PaddleOCR-VL-1.6 (Apache-2.0) |
| Seconds per page, measured with `python -m kaagaz.cli measure` on the build laptop on 30 Sep 2026 (RapidOCR, made-up files, AI off): a scanned page 5.4 to 10.1 (6.9 on average), a photo 6.6, a text-layer PDF page 0.05, a 200-row sheet 0.01. PaddleOCR-VL on the same CPU: 178 to 653 | The upload estimate uses 7 s a page for a PDF or photo (a PDF is estimated as a scan) and 1 for a sheet. The card and invoice calls add time when the AI is on; measure them once Ollama runs |
| Meaning search: `halfvec(1024)` on HNSW (m 16, ef_construction 64), iterative scan `relaxed_order`, `SEARCH_EF` 100; 50 candidates a list, RRF k 60, a 3 s statement timeout (2 Oct 2026, PROVISIONAL starting values) | The index is used even for a small customer, and vectors take half the space |
| 0006 casts the old vectors to `halfvec` instead of clearing them (2 Oct 2026, PROVISIONAL) | 3.1b's first PR shipped before `reembed` existed. They keep their old tag, so meaning search leaves them out until `reembed` |
| The exact scan when the index walk comes back short (2 Oct 2026, PROVISIONAL) | A small scope in a big table still gets its full 50 |
| A CHECK on `messages.scope` (an object, at most 4096 bytes), not in the PRD; the 0006 downgrade refuses while any message has a rewrite or a scope (2 Oct 2026, PROVISIONAL) | Bounded stored filters; a downgrade never loses saved data (the 0004 pattern) |
| What is embedded: the reduced header and text for a piece, the model card's instruction for a question, tag `MODEL_EMBED#ctx1`; `reembed` takes `--customer-id` (the PRD wrote `--customer`) (2 Oct 2026) | "Before:" would mix the previous piece into the vector, and the name would make every vector stale on a rename; the flag follows `cli.py` |
| Scope: a filter that matches nothing finds nothing; both date ends included; `/api/search` also takes the file type; a bad query string is 400, as everywhere in this API; `SEARCH_MAX_DOCUMENTS` 20 (1 to 100); `more` counts within the candidates (2 Oct 2026, PROVISIONAL) | An empty filter must never mean "everything" |
| Retry in Failed jobs is the existing Read again route, which also takes ready documents; the list shows current versions only (2 Oct 2026, PROVISIONAL) | No second route doing the same job |
| Review paging stays OFFSET (the plan asked for keyset); `links` is capped at 50 rows; the invoice panel shows for any document with an `invoices` row (2 Oct 2026, PROVISIONAL) | The screen's "Show more" asks by offset; a model-off fallback row is still worth showing |
| Every table and column carries a comment (0007, `alembic_version` too), and every later migration comments its own new columns (2 Oct 2026) | The NL-to-SQL agent reads `pg_description`; `test_every_table_and_column_has_a_comment` fails otherwise |
| Golden questions: 30, frozen by `QUESTIONS_SHA256`, never tuned on; each run scores right, wrong, correct and missed not-found, wrong-but-verified and recall@5, and the worst of 3 runs counts (2 Oct 2026, the `tags` and "right or not-found" count PROVISIONAL) | Measured before any search change, so later parts can't drift unseen |

| A follow-up keeps the previous scope, and `{}` widens it; a saved scope that no longer parses matches nothing (2 Oct 2026, PROVISIONAL) | A follow-up asks about the same documents; a scope is never widened by accident |
| A sentence backed only by a neighbour, or holding a number no cited hit holds, is needs review; an invoice whose check failed makes that document's pieces needs review on the search route and its figures on the fixed route (2 Oct 2026, PROVISIONAL) | Context is not evidence, and a figure from a failed check is never verified |
| The follow-up guard compares sets of words, so it can't see word order: "What did Mehta pay to Patil?" after "What did Patil pay to Mehta?" is accepted, and a metric can carry over from an earlier question (2 Oct 2026, PROVISIONAL) | Both show as "Searching for: …", so the person sees what was searched |
| H2 is partial: the chat picker offers all documents, one folder, or the documents chat was opened for; no picker of several documents from the list (2 Oct 2026, PROVISIONAL) | Vrushit decides whether that picker is wanted |
| "Ask about this document" shows only on the ready version in use, outside Trash (2 Oct 2026, PROVISIONAL) | Chat would find nothing in any other version |

**Golden run (G3): not yet run (needs the model on).** Record the date, the git SHA, the models and the worst run's totals here.

**Open**

| Decision | Blocks |
|---|---|
| What "verified" promises, in writing | The labels users see |
| The virus scanner and upload limits in AWS (values marked PROVISIONAL in `.env.example`) | Hosting |
| The go or no-go bar for launch, as a number | Launch |
| PRD 3 sign-off, and each PROVISIONAL row of 2 Oct 2026 above | Merging PRD 3 |

**Known limits**

1. **Word and typo indexes span all customers.** The GIN posting lists of `tsv`, `tsv_simple` and the trigram indexes hold every customer's pieces, and the customer filter comes after the index. Fine at this size; a per-customer partition is the upgrade.
2. **The index walk stops at `hnsw.max_scan_tuples` (20,000).** A small scope in a big table then runs the exact scan over the scope's pieces, which grows with the scope. Past that, a per-customer index or a higher limit is needed.
3. **Vectors are half precision (fp16),** so distances are rounded slightly against full precision.
4. **Migration 0006 is offline.** It holds ACCESS EXCLUSIVE locks on `pieces` and `messages` while it rewrites `pieces` once and builds three indexes, so search, chat and the worker all wait. It is timed only on the tests' 2,000 vectors: time it on a production-sized copy before release. The word and typo plans, and plans with a scope, are not measured at production size either.
5. **The snippet is the first 300 characters of the best piece,** so the matching words can fall outside it.
6. **`/api/search` embeds the raw `q`,** while chat embeds the English form of a Hinglish question. A Devanagari question with a typo is matched against `text` and `text_latin`, not against pieces written in Latin letters, and the Hinglish word map doesn't know the Latin spellings of many Devanagari words.
7. **`more` counts only within the 150 candidates,** not every match in the document.
8. **The answer check reads ASCII digits only** (`verify.numbers_in`). A number a drafted sentence writes in Devanagari digits is not seen, so it is not checked against its evidence. And a sentence giving in ASCII digits a figure printed in Devanagari digits ("कुल ₹ ४५०.००") is dropped, so the answer becomes not found. The fix (fold the evidence's digits first) belongs in its own PR.
9. **recall@5 comes from the answer's citations** until `run_golden.py` asks `/api/search`; the two numbers are not comparable.
10. **A word or typo list past the timeout fails the search** (503); only the meaning list has a fallback.
11. **The Failed jobs list scans the customer's documents** (no index leads on status); add a partial index on the failed states if customers grow large.
12. **The follow-up guard is strict,** so some good rewrites fall back to the question as asked (its rate with the real model is not measured yet).
13. **Neighbours are dropped first** when the model window is full; every hit is kept.
14. **3.3a's neighbour token cut is a stand-in.** When `fix/s2-answer-check-atoms`, `fix/s2-answer-model-budget` and `perf/s2-query-wins` merge, 3.3a is rebased onto them and their evidence window cap (`fit_evidence`) replaces it.
15. **Not yet checked with the model on:** the model card's example scores for the query instruction in Ollama, and the reduced header's choice against the golden set.

Not built yet, against the Document-to-SQL plan: typed tables beyond invoices (land records, bank statements). The typed `invoices` and `invoice_lines` tables and their checks came in PRD 2 (step 5c), and the search tool outside chat (`GET /api/search`) in PRD 3.
