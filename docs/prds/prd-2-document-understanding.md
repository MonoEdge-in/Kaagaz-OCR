# PRD 2: Document Understanding

> **Product:** Kaagaz · **PRD 2 of 3** · **Build:** Wed 30 Sep – Thu 1 Oct 2026 · **Baseline:** `main` at `b088396` (PRD 1 and its follow-ups merged)
> **PRD owner:** Vrushit Patel · **Built by:** Tushar Saini · **Status:** in build. Draft PRD; sign-off from Vrushit pending.

## Where the product is

| Stage | What it delivers | State |
|---|---|---|
| **Sprint 1** | Upload with virus scan, reading PDFs, photos and sheets, pieces and labelled values, chat answers with sources, review queue, admin console | ✅ Merged |
| **PRD 1: Accounts and Files** | Safer sign-in, admin two-step codes, lockout, folders and tags, upload limits by type, trash and versions, 5 bug fixes | ✅ Merged: #37, #38, #39 |
| **PRD 2: Document Understanding** | Page routes, PaddleOCR-VL, Hindi and Marathi, pieces that cross pages, a card per document, invoice tables with checks, reprocess | 🚧 This document, in 6 PRs |
| **PRD 3** | Better search (embeddings, HNSW, text in Latin letters), search inside a document, viewer panels, golden questions, handover | ⏳ Next |

---

## Part 1: What PRD 1 delivered (merged)

| Work package | What it added | Key numbers |
|---|---|---|
| **WP1.1: Known bugs** | **B1:** a document whose last queue try fails ends `failed · retries_exhausted`, and never stays "Reading…"; the heartbeat now beats between pages. **B2:** invoice totals count once. **B3:** chat survives bad embedding vectors. **B4:** a reviewer can't overwrite a verified value (409 `not_needs_review`). **B5:** no row values in logs. | 5 bugs, each with a test written first |
| **WP1.2: Schema** | Migration 0002: sign-in columns on `users`; `folders`, `tags` and `document_tags` with row level security; folder, trash and version columns on `documents`. Helpers for folders (tree lock), tags and trash (family lock, purge with `SKIP LOCKED`) | 2 deadlocks found in review and fixed |
| **WP1.3: Admin sign-in** | `/admin/login` with TOTP codes (RFC 6238), a secret sealed with AES-GCM, a lockout counted atomically, sessions that end on a password change or "sign out everywhere", temporary passwords, and Unlock / Reset password / Reset 2FA | — |
| **WP1.4: Upload limits** | A limit per file type (PDF, photo or scan, sheet), a guard on the request size before anything is read, and page pictures stored as WebP | — |
| **WP1.5: Folders and tags** | A folder tree, tags, rename and move, and list filters (folder, tags, type, dates) | — |
| **WP1.6: Trash and versions** | Move to Trash, Restore, Delete forever, new versions (the highest ready version is current), an hourly purge, and Trash and older versions left out of search and answers | — |
| **#38: Optional admin 2FA** | Each admin chooses to turn 2FA on or off (with the password). Set-up by scanning a QR code from our own encoder (no new library) | QR decoded by OpenCV on 16 of 16 tests |
| **#39: PRD 1 follow-ups (Vrushit)** | Job attempts shown as "Try N of 5" (migration 0003), and fixes across chat, views, trash, listing and the worker | — |

**Checks at the baseline:** about 1,497 backend tests (82 of them customer-isolation tests) and 398 frontend tests. ruff, mypy strict, eslint and tsc are all clean.

---

## Part 2: PRD 2 goal and exit test

**Goal.** Read business documents (invoices, bills, statements, contracts, land records) in **English, Hindi or Marathi**. Pick the right way to read each page, join text across page breaks, describe each document with a card whose every fact points to its source, and turn invoices into typed tables with checks. Every answer and summary is in English.

**Exit test.** Upload these 6 files:
- a 3-page textile invoice;
- a Hindi retail bill;
- a Marathi land record;
- a bank statement;
- a corrupted PDF;
- a 20-page report with a value that crosses a page.

Then all of these must hold:
1. Every page shows its route: `cells`, `text_layer`, `ocr` or `blank`.
2. The profit value that crosses the page break is stored in **one piece**, with `page_from=15`, `page_to=16`, and a region on both pages.
3. The document card shows the type, languages, summary, parties (with Latin spellings for Devanagari names), dates and identifiers, each citing where it was found.
4. The textile invoice gives 1 invoice row plus its line rows, and its checks pass. A copy with a changed amount fails with `lines_sum_mismatch`.
5. Reprocess makes a new run, and the old results are **replaced, not duplicated**.
6. With `MODEL_ENABLED=false`, all 6 files still reach `ready`. There is no card; the invoice values come from the existing label rules.

---

## Part 3: Requirements

### Page routes: R1–R6
| ID | Requirement |
|---|---|
| R1 | Each page gets a `route`: `cells` (a sheet), `text_layer` (a PDF text layer that passes the quality test), `ocr` (scanned) or `blank` (no text). |
| R2 | Quality test for the text layer: at least 40 letters or digits, and at least 90% good characters (Latin, Devanagari U+0900–097F, punctuation). If an image covers more than 50% of the page and the text is under 200 characters, it is a scan. Old Hindi fonts (Devanagari encoded as Latin) fail the good-character rule. |
| R3 | A blank page (grey-level standard deviation < 3.0) skips OCR. |
| R4 | `pages.effective_dpi` comes from the largest image. Below 150 DPI, the page gets `low_resolution = true`. |
| R5 | OCR runs at 300 DPI in memory, because Devanagari vowel signs need it. The stored page picture stays a 150 DPI WebP. |
| R6 | Each run counts pages by route, vision crops, model calls and seconds (`processing_runs`). |

### PaddleOCR-VL: P1–P5
| ID | Requirement |
|---|---|
| P1 | Its own `ocr` container (`backend/Dockerfile.ocr`): FastAPI on port 8080, with the Paddle libraries kept inside it and no model download at runtime. |
| P2 | `POST /read` (a PNG at the OCR DPI) returns JSON: `engine`, `version`, the dimensions, and `blocks[]` (kind, text, order, box as fractions of the page, table metadata). `GET /health`. Block kinds: title, heading, paragraph, list_item, table_cell, header, footer, stamp, signature, image, formula, other. 422 for an unreadable image; 503 while the model loads. |
| P3 | `PaddleVlEngine` implements `OcrEngine` over `OCR_URL`. `OcrLine` gains optional `kind` and `table`. `OCR_ENGINE=paddleocr_vl`, with `rapidocr` as the fallback for weak hardware. |
| P4 | `Span` gains optional `kind` and `origin` (`cell`, `text_layer`, `ocr`). Table cells carry `table_id`, `row` and `col`. The JSON is written only when set. |
| P5 | OCR down, or a timeout (300 s), raises `BackendUnavailable` and the job is retried. A 422 fails the document with `corrupt_file`. |

### Languages: L1–L4
| ID | Requirement |
|---|---|
| L1 | English, Hindi and Marathi (Hindi and Marathi share the Devanagari script). Every answer and summary is in English. |
| L2 | A piece is Devanagari if at least 50% of its letters are in U+0900–097F. `documents.languages` comes from the card; without a model it is `en` for Latin text. |
| L3 | Devanagari to Latin: IAST transliteration (`indic-transliteration`), strip accents (NFKD), lowercase, drop a word-final "a" after a consonant. पाटील → patil; शिंदे → shinde. |
| L4 | Devanagari digits ०–९ become 0–9 for value parsing and quote matching. `value_text` keeps the printed form. |

### Pieces that cross pages: C1–C7
| ID | Requirement |
|---|---|
| C1 | A new piece starts only at a title or heading, at the start or end of a table, at a blank-line gap (1.5 × line height), or at 800 characters. **A page break never splits a piece.** |
| C2 | Running headers and footers (repeated in the top or bottom 8% of 3 or more pages) are left out of pieces; they stay in `reader_outputs`. |
| C3 | Each piece keeps the headings above it (title, then heading), carried across pages until replaced. |
| C4 | `pieces.context_header` = "Document: … \| Type: … \| Date: … \| Section: … \| Pages n–m \| Before: (the last 120 characters of the previous piece)". |
| C5 | A table piece holds up to 20 rows or 800 characters, with rows written as "Header: value \| Header: value". A table that runs over several pages keeps the earlier headers when the column count matches. |
| C6 | `pieces.seq` keeps one order across the whole document (PRD 3 uses it to fetch neighbours). |
| C7 | **Required fixture:** a 20-page PDF with a "Page n of 20" footer. Page 15 ends "Profit of the month Sept is" and page 16 starts "$12,000". This gives exactly one piece holding both, `page_from=15`, `page_to=16`, with regions on both pages, and no piece contains the footer. |

### Document card: D1–D6
| ID | Requirement |
|---|---|
| D1 | One model call per document (`chat_json`). The schema: `doc_type`, `languages`, `title` (≤ 120 characters), `summary` (≤ 600), `document_date`, `parties` (role + name_quote), `identifiers` (kind + value_quote) and `key_facts` (up to 20). The input is capped at 12,000 characters: the first 2 pages, every heading, and the last page. |
| D2 | **The quote rule.** Normalise both sides (NFKC, collapsed spaces, Devanagari digits as ASCII, case ignored for Latin). Each quote must be found word for word in reading order, across pages. A `value_quote` is searched within 300 characters after its `label_quote`. A quote that isn't found is dropped and counted in `dropped_quotes`. **The stored text is always the reader's text at the match**, never the model's. |
| D3 | `documents.doc_type`, `languages`, `summary`, `document_date` and `card` (validated, with page and box). Parties, identifiers and key facts go into `extracted_values` with `record_key='card'`, each with the pages and regions of both the value and its label. |
| D4 | Every Devanagari party name also gets its `name_latin` (the L3 spelling). |
| D5 | `document_links` join current, ready documents (not in Trash) that share an identifier: invoice, PO, challan, survey, khata, account or reference numbers, compared without spaces and ignoring case. GSTIN and PAN are never used for links. Links are replaced on every run. |
| D6 | Without a model there is no card, type or links, and the list shows "Unknown". |

### Invoice tables and checks: E1–E5, K1–K3
| ID | Requirement |
|---|---|
| E1 | The first typed document type is `invoice` (it covers retail, textile and trading bills). |
| E2 | For invoices, with a model: split the reader text into windows of 6,000 characters with 1,000 of overlap (at most 10 windows), and make one `chat_json` call per window for the fields and the lines, all as quotes. |
| E3 | Merging windows: the first quote found for a field wins, and a later different value adds `conflicting_values`. Lines are deduplicated by the location of their `amount_quote`. |
| E4 | These map to the existing fields (`party_name`, `gstin`, `tax_amount`, `total_amount`). Document fields use `record_key='document'` and lines `'line:{n}'`. With the model on, they **replace** the rule-based values; the two are never both stored. |
| E5 | `invoices` and `invoice_lines` rows are built from `extracted_values` in the same transaction. A row is `verified` only when every non-null field is verified. |
| K1 | Checks, with an allowance of ±1.00: the lines add up to the subtotal (leaving out brought and carried forward); subtotal + tax = total; the invoice number and the total are present; the date is between 2000-01-01 and tomorrow; each GSTIN matches the pattern and its mod-36 checksum. |
| K2 | `invoices.check_status` is passed, failed or not_checked, and `check_reasons` holds lines_sum_mismatch, total_mismatch, missing_invoice_number, missing_total, bad_date, bad_gstin, conflicting_values or truncated. |
| K3 | A failed check sets the values involved to `needs_review · check_failed`, even when the readers agreed, so they show in the review queue. |

### Reprocess and progress: X1–X4
| ID | Requirement |
|---|---|
| X1 | `POST /api/documents/{id}/reprocess` (reviewer or admin) for ready, failed or needs_reader documents that are not in Trash. It sets the document to received, queues it and replaces the results; remembered fixes are applied again. The confirm step says: "Reviewer fixes (N) return only if saved as remembered fixes". |
| X2 | `processing_runs`: one row per run, with the engines, models and counts, written at the start (running) and at the end. |
| X3 | `documents.progress` = {step, done, total}, where step is scanning, preparing, reading, understanding or indexing. It is updated after each page in a short transaction, never inside save. The screen shows "Reading page 3 of 40". |
| X4 | `python -m kaagaz.cli measure --file <path>` prints each page's route and seconds. Measure 5 samples, and use the measured seconds per page to replace the upload estimate of "8 seconds per MB". |

---

## Part 4: Data changes (migration `0004_understanding`, down from `0003`)

Every new customer table gets `customer_id`, `(customer_id, id)` composite keys, the `customer_only` row-level-security policy, the app-role grants and an isolation test.

| Table | Change |
|---|---|
| `pages` | + `route`, `effective_dpi`, `low_resolution` |
| `documents` | + `doc_type`, `languages`, `summary`, `document_date`, `card`, `progress` |
| `pieces` | + `context_header`, `text_latin` |
| `extracted_values` | + `label_page_no`, `label_region`; `label_reason` also allows `check_failed` |
| `invoices` (new) | One row per invoice document: number, date, seller and buyer (names and GSTINs), subtotal, tax, total, currency, label, check status and reasons |
| `invoice_lines` (new) | line_no, description, HSN, quantity, unit, rate, amount, label |
| `document_links` (new) | Documents that share an identifier |
| `processing_runs` (new) | One row per reading run: engines, models, pages by route, crops, model calls, dropped quotes, seconds |

**New settings** (required, with no hidden defaults):
- `OCR_ENGINE=paddleocr_vl`, `OCR_URL`, `OCR_TIMEOUT_SECONDS=300`, `OCR_DPI=300`;
- `TEXT_LAYER_MIN_CHARS=40`, `TEXT_LAYER_MIN_GOOD_RATIO=0.9`, `SCAN_IMAGE_COVERAGE=0.5`;
- `BLANK_PAGE_STDDEV=3.0`, `LOW_RES_DPI=150`;
- `CARD_MAX_CHARS=12000`;
- `EXTRACT_WINDOW_CHARS=6000`, `EXTRACT_WINDOW_OVERLAP=1000`, `EXTRACT_MAX_WINDOWS=10`.

**Libraries (approved in the PRD):** `paddleocr` and `paddlepaddle` (Apache-2.0, installed only in `Dockerfile.ocr`) and `indic-transliteration` (MIT). Standard `html.parser` reads OCR tables.

---

## Part 5: The build, in 6 PRs

| PR | Branch | Work packages | What it adds | Depends on |
|---|---|---|---|---|
| 1 | `feat/s2-understanding-schema` | WP2.1 | Migration 0004, the pipeline store saving the new tables in one transaction, isolation tests | main |
| 2 | `feat/s2-paddleocr-vl` | WP2.2 | The `ocr` container, `PaddleVlEngine`, block kinds and tables, OCR at 300 DPI, a fake OCR server for CI | PR 1 |
| 3 | `feat/s2-page-route` | WP2.3 + WP2.7 | Page routes, blank and low-resolution pages, runs and progress ("Reading page 3 of 40"), **Reprocess**, `cli measure`, measured time estimates | PR 1, PR 2 |
| 4 | `feat/s2-cross-page-pieces` | WP2.4 | Pieces that cross pages, headers and footers left out, section headings, table pieces, Hindi and Marathi detection, Latin spellings, Devanagari digits | PR 1 |
| 5 | `feat/s2-document-card` | WP2.5 | The document card through the quote rule (`extraction/quotes.py`), parties with Latin names, related documents | PR 4 |
| 6 | `feat/s2-invoice-tables` | WP2.6 | Invoice and line tables, window extraction, the 5 checks, failed checks sent to review | PR 5 |

**How it is built:**
- **Tests first:** each behaviour gets a failing test, then the code. The test commit and the code commit are separate.
- **Customer isolation:** every new table and route has a test where one customer uses another's ids and gets 404, never 403.
- **Concurrency:** lock order is documented. Short transactions for progress. `SKIP LOCKED` where work is shared. Tests force the order instead of sleeping.
- **Model safety:** the model never writes a stored value. It only returns quotes, and the code finds them in the reader's text. Everything works with `MODEL_ENABLED=false`.
- **Reproducibility (MLOps):** every run records the engine, the model tags, the counts and the seconds (`processing_runs`), so results can be compared across models and settings.

**Before use, verify:** the PaddleOCR-VL API and output fields in the pinned version; the Ollama tag for Qwen3.5 4B and whether it accepts images; Ollama's `format` JSON schema and the `images` field; the `indic-transliteration` API; peak memory on a 16 GB laptop.

---

## Out of scope (PRD 3 or later)
- **PRD 3:** embedding upgrade (halfvec, HNSW, embedding the context header); word search on `text_latin`; search inside a document and neighbour pieces; viewer panels (the card, related documents, low-resolution warning); the failed-jobs tab; golden questions; the handover pack.
- **Not in any PRD:** typed tables beyond invoices; current vs former owner logic; scripts other than Latin and Devanagari; GPU and vLLM; a re-ranker; Temporal; NL-to-SQL; AWS hosting.

## Sign-off
| Role | Who | State |
|---|---|---|
| PRD approval | Vrushit Patel | Pending |
| Libraries (paddleocr, paddlepaddle, indic-transliteration) | Vrushit Patel | Pending |
| Exit test | Vrushit Patel | Pending |
