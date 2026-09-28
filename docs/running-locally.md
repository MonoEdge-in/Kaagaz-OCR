# Running Kaagaz on a laptop

Two ways: everything in Docker, or the stand-ins in Docker and the API,
worker and web app run straight on the laptop. Every key and password in
`.env.example`, `docker-compose.yml` and `infra/` is a **laptop-only
placeholder**, never a real secret.

## What runs where

| Service | Port | What it is | In AWS |
|---|---|---|---|
| `frontend` | 3000 | Next.js web app (open this) | the same image |
| `api` | 8000 | FastAPI, `/api/*` | the same image |
| `worker` | none | `python -m kaagaz.worker_main`, one job at a time | the same image |
| `migrate` | none | one-shot: `alembic upgrade head`, then makes the storage bucket | a deploy step |
| `postgres` | 5432 | PostgreSQL 17 with pgvector | Amazon RDS |
| `seaweedfs` | 8333 | file storage, S3 API | Amazon S3 |
| `elasticmq` | 9324 | job queue `kaagaz-jobs` and its failure bin `kaagaz-jobs-failed`, SQS API | Amazon SQS |
| `clamav` | 3310 | clamd virus scanner | ClamAV (PROVISIONAL) |
| `ollama` | 11434 | local AI, optional (`--profile ollama`) | PROVISIONAL |

## 1. Settings

```
cp .env.example .env                 # bash
Copy-Item .env.example .env          # PowerShell
```

Then set `SESSION_SECRET` in `.env` to at least 32 random characters. The
placeholder is too short on purpose, so nothing starts until you do:

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`MODEL_ENABLED=false` (the default) runs everything with no AI. To use
Ollama, pull `MODEL_CHAT` and `MODEL_EMBED` first, then set it to `true`.

## 2a. Everything in Docker

```
docker compose up --build
```

Open http://localhost:3000.

* The first start of `clamav` can take several minutes while it updates its
  signatures. The worker waits for it; the web app and API work meanwhile.
* The frontend image bakes in `BACKEND_URL=http://api:8000` when it is built
  (Next.js writes the `/api` rewrite into the build).
* The containers reach Ollama on the laptop at `host.docker.internal:11434`.
  To run Ollama in Docker instead, set `DOCKER_MODEL_BASE_URL=http://ollama:11434`
  in `.env` and run `docker compose --profile ollama up --build`, then pull the
  models once: `docker compose exec ollama ollama pull <model>`.
* Stop with `docker compose stop` (the worker finishes the job in hand).
  `docker compose down -v` also deletes the data volumes.

## 2b. No Docker for the app

Start only the stand-ins in Docker:

```
docker compose up postgres seaweedfs elasticmq clamav
```

No Docker at all: point `DATABASE_URL`, `STORAGE_*` and `QUEUE_*` in `.env`
at your own PostgreSQL 16+ (with the `vector` and `pg_trgm` extensions),
S3-compatible store and SQS-compatible queue. Without ClamAV, set
`SCANNER=eicar_dev`: **laptop only**, it finds the EICAR test file and
nothing else, so never use it with real files.

Then, from `backend/` with its virtual environment (`backend/.venv`), with
the variables from `.env` loaded into the shell:

```
pip install -e ".[dev,ocr]"            # once
alembic upgrade head
uvicorn kaagaz.api.main:app --port 8000
python -m kaagaz.worker_main          # in a second terminal
```

The worker makes the storage bucket on its first start if it is missing.
Ctrl+C stops it after the job in hand.

And the web app, from `frontend/`:

```
npm ci                                 # once, Node 20 or later
npm run dev                            # http://localhost:3000
```

It forwards `/api/*` to `BACKEND_URL` (default `http://localhost:8000`).

## 3. Demo customers

With the database migrated, from `backend/` (or `docker compose exec api` in
front of it):

```
KAAGAZ_DEMO_PASSWORD='<a password of 12+ characters>' python -m kaagaz.cli seed-demo
```

It makes two customers, "Sharma Traders" and "Mehta Textiles", each with an
admin, a reviewer and a member, and prints their e-mails. The password is
never a command-line flag.

## 4. Tests

Backend, from `backend/` (`.venv/Scripts/` on Windows, `.venv/bin/` elsewhere):

```
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/mypy.exe .
.venv/Scripts/python.exe -m pytest -m isolation      # customer separation first
.venv/Scripts/python.exe -m pytest -m "not db"       # no database needed
```

Tests that need PostgreSQL carry the `db` marker and read `DATABASE_URL`. Point
it at a separate, throwaway database (for example `kaagaz_test`), never the
one with your data, then run everything:

```
.venv/Scripts/python.exe -m pytest
```

The adapter tests (`tests/test_adapter_*.py`) need no services: S3 and SQS
are stubbed with `botocore.stub.Stubber`, and clamd is a fake server on
127.0.0.1.

Frontend, from `frontend/`:

```
npx tsc --noEmit
npx eslint .
npx vitest run
```

## 5. Check that each part works

With the stack running (step 2a), each check says what to run, what you should see, and what counts as a fail. What isn't built yet is listed at the end of [architecture.md](architecture.md).

**Samples.** Use made-up documents, never real client files, and keep them in `samples/`, which git ignores:

| File | Used by |
|---|---|
| `invoice.pdf`: a digital PDF invoice with "Invoice No", "Invoice Date", "GSTIN" and "Grand Total" printed on it | Checks 1, 3, 4, 5 |
| `invoice-scan.png`: a photo or scan of a printed invoice | Checks 1, 3, 4, 6 |
| `register.xlsx`: a spreadsheet with a header row and a few rows | Checks 1, 3 |
| `register.csv`: the same as CSV | Check 1 |
| `note.jpg`: a photo of a handwritten note | Check 3 |
| `letter.docx`: any Word file | Check 1 (must be refused) |
| `eicar.txt`: the harmless EICAR antivirus test file, from the EICAR website | Check 2 (must be rejected) |

**Health check:**

```bash
curl -s http://localhost:8000/api/health
```

**Pass:** `{"status":"ok","database":"ok","storage":"ok","queue":"ok"}`.
**Fail:** HTTP 503 with any value set to `error`.

**Sign in.** Make the demo customers (step 3) with the password `demo-password-123`, then sign in as Sharma Traders' reviewer, who can do every check below. The cookie is saved to `cookies.txt`:

```bash
curl -s -c cookies.txt -H 'X-Requested-With: kaagaz' -H 'Content-Type: application/json' -d '{"email":"reviewer@sharma-traders.example","password":"demo-password-123"}' http://localhost:8000/api/auth/login
```

**Pass:** a JSON reply with the user's name and role `reviewer`.

Every POST and DELETE needs the `X-Requested-With: kaagaz` header. Without it, the API answers `403 csrf`.

**Two shortcuts** used below. Paste them into your shell once:

```bash
db() { docker compose exec -T postgres psql -U kaagaz -d kaagaz -c "$1"; }
up() { curl -s -b cookies.txt -H 'X-Requested-With: kaagaz' -F "file=@$1" http://localhost:8000/api/documents; echo; }
```

`db` runs SQL as the database owner, so it sees every customer's rows. The app itself only ever sees one customer's.

### 1. Takes in any English business document

**Upload one of each type:**

```bash
for f in invoice.pdf invoice-scan.png register.xlsx register.csv note.jpg; do up samples/$f; done
```

**Pass:** each reply is HTTP 201 with `"duplicate": false` and a document `id`.

**The worker finishes each one.** Wait about a minute, then:

```bash
db "SELECT display_name, detected_type, status, failure_code, page_count FROM documents ORDER BY created_at"
```

**Pass:** every row ends at `status = ready`, with a `page_count`. The status moves from `received` to `clean` to `ready`.
**Fail:** `failed` (read `failure_code`), or `needs_reader`, which means no reader handles that type yet.

**Files it must refuse:**

```bash
up samples/letter.docx
```

**Pass:** HTTP 400 with the code `unsupported_type`. A file over `MAX_UPLOAD_MB` (50) gives 413 `too_large`, and an empty file gives 400 `empty_file`.

**Through the web app:** open <http://localhost:3000>, sign in as the reviewer, and drag in the same files together.
**Pass:** each row appears and reaches Ready without reloading the page.

---

### 2. Manages the files

**The original is stored once, where the code says.** In SeaweedFS each original sits at `customers/<customer_id>/documents/<document_id>/original`. Download it back through the API and compare it with your file:

```bash
ID=$(db "SELECT id FROM documents WHERE display_name='invoice.pdf'" | sed -n 3p | tr -d ' ')
```

```bash
curl -s -b cookies.txt -L -o /tmp/back.pdf http://localhost:8000/api/documents/$ID/file && sha256sum samples/invoice.pdf /tmp/back.pdf
```

**Pass:** both fingerprints are identical.

**Duplicates are caught by fingerprint:**

```bash
up samples/invoice.pdf
```

**Pass:** HTTP 200 (not 201), `"duplicate": true`, and the same `id` as the first upload. `db "SELECT count(*) FROM documents WHERE display_name='invoice.pdf'"` still says 1.

**Infected files are stopped before anything reads them:**

```bash
up samples/eicar.txt
```

**Pass:** the document ends at `status = rejected`. It has no rows in `pages`, `pieces` or `extracted_values`, and it never becomes readable, even if processed again.

**Every action is logged:**

```bash
db "SELECT action, target_type, created_at FROM audit_log ORDER BY created_at DESC LIMIT 10"
```

**Pass:** `auth.login` and one `document.upload` for each new file. A duplicate upload adds no new entry.

**Delete:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -b cookies.txt -H 'X-Requested-With: kaagaz' -X DELETE http://localhost:8000/api/documents/<id>
```

**Pass:** `204`. The document, its pages, pieces and values are gone, and `GET /api/documents/<id>` now answers 404.

**Customers can't see each other's files.** Sign in as `reviewer@mehta-textiles.example` into a second cookie file and list documents:

```bash
curl -s -c other.txt -H 'X-Requested-With: kaagaz' -H 'Content-Type: application/json' -d '{"email":"reviewer@mehta-textiles.example","password":"demo-password-123"}' http://localhost:8000/api/auth/login >/dev/null && curl -s -b other.txt http://localhost:8000/api/documents
```

**Pass:** `"total": 0`, and opening one of Sharma Traders' document ids gives 404.

**Not built yet:** folders, tags and versions. There are no endpoints or tables for them.

---

### 3. Reads every page into positioned text

**Pages exist, with a picture where the file has one:**

```bash
db "SELECT d.display_name, p.page_no, p.width, p.height, p.unit, p.has_image FROM pages p JOIN documents d ON d.id = p.document_id ORDER BY 1, 2"
```

**Pass:** one row per page. PDFs are measured in `pt`, photos in `px` and spreadsheets in `cells`, with one page per sheet. PDFs and photos have `has_image = true`.

**What each reader produced:**

```bash
db "SELECT d.display_name, r.role, r.reader, jsonb_array_length(r.output) AS spans FROM reader_outputs r JOIN documents d ON d.id = r.document_id ORDER BY 1, 2"
```

**Pass:** each ready document has a `first` reading with more than 0 spans. The digital PDF has a `second` reading too.

**Text pieces carry their position:**

```bash
db "SELECT d.display_name, pc.page_from, left(pc.text, 60) AS text, jsonb_array_length(pc.regions) AS boxes FROM pieces pc JOIN documents d ON d.id = pc.document_id ORDER BY 1, pc.seq LIMIT 20"
```

**Pass:** the text matches what's printed on the page, and every piece has at least one box.

**Check by eye.** Open a document in the web app. Its page picture comes from `/api/documents/<id>/pages/1/image`, and a spreadsheet's grid from `/api/documents/<id>/pages/1/cells`.
**Pass:** the highlighted boxes sit on the words they claim.

**Scans:** `invoice-scan.png` goes through OCR (`OCR_ENGINE=rapidocr`).
**Pass:** its pieces contain the invoice's words. Setting `OCR_ENGINE=none` and uploading a new scan should give `needs_reader` instead.

**Handwriting:** upload `note.jpg` and read its pieces.
**Pass:** most words come out readable. RapidOCR reads print, not handwriting, so expect this one to be weak. Handwriting only gets a second read when `MODEL_ENABLED=true` and `MODEL_VISION` names a vision model.

---

### 4. Extracts the fields

```bash
db "SELECT d.display_name, v.record_key, v.field, v.value_text, v.value_number, v.value_date FROM extracted_values v JOIN documents d ON d.id = v.document_id ORDER BY 1, 3"
```

**Pass, for the invoice PDF:** rows for the fields printed on it. The fields it looks for are `invoice_number`, `invoice_date`, `total_amount`, `subtotal`, `tax_amount`, `gstin`, `party_name` and `buyer_name`. Amounts fill `value_number` and dates fill `value_date`, matching the printed values exactly.
**Pass, for the spreadsheet:** one `record_key` per row, and a field per column.
**Fail:** a printed field with no row, or a wrong number or date.

**Identity numbers are kept as read.** Put a made-up PAN or Aadhaar number in a sample and upload it.
**Pass:** it appears in full, exactly as printed, in `pieces.text` and in `extracted_values.value_text`.

**Not built yet:** document-type detection, and the typed `invoices` and `invoice_lines` tables. Every value sits as one row per field in `extracted_values`.

---

### 5. Proves every value

Every value must point back to its page, its box, the reader and a confidence score.

```bash
db "SELECT count(*) AS values, count(*) FILTER (WHERE region IS NULL OR region = '{}'::jsonb) AS no_box, count(*) FILTER (WHERE page_no IS NULL) AS no_page, count(*) FILTER (WHERE reader IS NULL) AS no_reader FROM extracted_values"
```

**Pass:** `no_box`, `no_page` and `no_reader` are all 0. The database refuses a value without them anyway.

```bash
db "SELECT field, value_text, page_no, region, reader, confidence FROM extracted_values LIMIT 10"
```

**Pass:** `region` holds the box coordinates on that page. `confidence` is between 0 and 1 for OCR readings, and empty for values read straight from a spreadsheet cell or a PDF's text.

**Check by eye:** in the web app, click a value on the document page.
**Pass:** the highlight lands on exactly the printed value.

---

### 6. Checks the values, and a person fixes the doubtful ones

**Every value is labelled, with a reason:**

```bash
db "SELECT label, label_reason, count(*) FROM extracted_values GROUP BY 1, 2 ORDER BY 1, 2"
```

**Pass:**
- Spreadsheet cells are `verified` with the reason `direct_cell`.
- PDF values are `verified` with `readers_agree` when both readings match, and `needs_review` with `readers_disagree` when they don't.
- Scan values with one reading are `needs_review`, with `single_reader` or `low_confidence`.

**The review queue:**

```bash
curl -s -b cookies.txt http://localhost:8000/api/review
```

**Pass:** it lists exactly the `needs_review` values, each with its document, page and box. A member-role user gets 403.

**Fix one:**

```bash
curl -s -b cookies.txt -H 'X-Requested-With: kaagaz' -H 'Content-Type: application/json' -d '{"choice":"typed","value":"12,345.00","remember":true}' http://localhost:8000/api/review/<value_id>
```

**Pass:**
- The value is now `verified`, with the reason `corrected`.
- `db "SELECT old_value, new_value, choice FROM corrections"` shows the fix and who made it.
- Because `remember` was `true`, `learned_corrections` holds a new row.
- Uploading another document with the same misreading applies the fix by itself, with the reason `learned_correction`.

The choices are `first` (keep the first reading), `second` (take the second reading) and `typed` (enter the right value).

**Not built yet:** rule checks such as "lines plus tax must equal the total", and required-field checks. A value is doubted only when the readers disagree or the confidence is low.

---

### 7. Makes it searchable

**Pieces are indexed for keyword and typo search:**

```bash
db "SELECT count(*) AS pieces, count(embedding) AS with_vectors FROM pieces"
```

```bash
db "SELECT left(text, 80) FROM pieces WHERE tsv @@ plainto_tsquery('english', 'grand total') LIMIT 5"
```

**Pass:** the second query finds the pieces holding that phrase. With `MODEL_ENABLED=false` (the default), `with_vectors` is 0, which is expected: meaning search is off, and keyword and typo search still work.

**Meaning search:** pull the two models, then set `MODEL_ENABLED=true` and restart:

```bash
ollama pull qwen3:4b && ollama pull qwen3-embedding:0.6b
```

```bash
docker compose up -d api worker
```

Re-upload a document. **Pass:** `with_vectors` rises to match `pieces` for that document.

**Search through the API:** search is only reachable through the chat endpoint, which searches the pieces and quotes the evidence it found.

```bash
curl -s -b cookies.txt -H 'X-Requested-With: kaagaz' -H 'Content-Type: application/json' -d '{"question":"What is the grand total on the invoice?"}' http://localhost:8000/api/chat
```

**Pass:** the reply quotes the invoice and cites its document and page. A question about something no document contains returns "I couldn't find this in your documents."

### Sign-off

| # | Capability | Pass / fail | Notes |
|---|---|---|---|
| 0 | Stack healthy, sign-in works | | |
| 1 | Every file type uploads and reaches ready; bad types refused | | |
| 2 | Original stored intact, duplicates caught, infected file rejected, actions logged, delete works, customers separated | | |
| 3 | Pages, readings and positioned pieces for every type | | |
| 4 | Invoice and spreadsheet values extracted correctly; identity numbers kept as read | | |
| 5 | Every value has a page, box, reader and confidence | | |
| 6 | Labels right; review queue, fix and remembered fix all work | | |
| 7 | Keyword search works; meaning search with models on; chat cites sources | | |
