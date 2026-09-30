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
| `ocr` | none | PaddleOCR-VL scan reader, optional (`--profile paddle`) | PROVISIONAL |

## 1. Settings

```
cp .env.example .env                 # bash
Copy-Item .env.example .env          # PowerShell
```

Then set `SESSION_SECRET` and `TOTP_ENCRYPTION_KEY` in `.env`, each to at
least 32 random characters and different from each other. Both placeholders
are too short on purpose, so nothing starts until you do. Run this twice:

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`MODEL_ENABLED=false` (the default) runs everything with no AI. To use
Ollama, pull `MODEL_CHAT` and `MODEL_EMBED` first, then set it to `true`.

An older `.env` needs these added from `.env.example` (a missing setting stops
the start with its name): `TOTP_ENCRYPTION_KEY`, `LOGIN_MAX_FAILURES`,
`LOGIN_LOCK_MINUTES`, `ADMIN_SESSION_MINUTES`, `MAX_IMAGE_MB`, `MAX_SHEET_MB`,
`TRASH_DAYS`, `JOB_MAX_ATTEMPTS`, `OCR_URL`, `OCR_TIMEOUT_SECONDS` and
`OCR_DPI`, plus the new `MAX_PAGES`, `MAX_IMAGE_PIXELS` and
`QUEUE_LEASE_SECONDS` values. Recreate the queue too (`docker compose up -d
--force-recreate elasticmq`): its failure-bin limit is now 10.

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
* PaddleOCR-VL reads scans with `OCR_ENGINE=paddleocr_vl` and
  `docker compose --profile paddle up --build`. Its container needs 10 GB of
  memory or more: on a 16 GB laptop, run it without Ollama and raise Docker
  Desktop's WSL2 memory. It is slow on a CPU: 3 to 11 minutes a page on a
  4-core laptop, plus about 5 minutes to load. A worker outside Docker reaches
  it with `-f docker-compose.ocr-host.yml` added (port 8080 on 127.0.0.1). Risk:
  that override gives the ocr container a way out to the internet (its own
  `ocr_host` network, never `default`), so use it only when you need it.
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
Ctrl+C stops it after the job in hand. At most once an hour it also deletes
for good what has been in Trash longer than `TRASH_DAYS` days (30).

And the web app, from `frontend/`:

```
npm ci                                 # once, Node 20 or later
npm run dev                            # http://localhost:3000
```

It forwards `/api/*` to `BACKEND_URL` (default `http://localhost:8000`).

## 3. Demo customers

With the database migrated, in Docker:

```
docker compose exec -e KAAGAZ_DEMO_PASSWORD='<a password of 12+ characters>' api python -m kaagaz.cli seed-demo
```

Or from `backend/` with the variables from `.env` loaded:

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
.venv/Scripts/python.exe -m pytest -m "not db"       # no database needed
.venv/Scripts/python.exe -m pytest -m isolation      # customer separation; most need DATABASE_URL
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

## 5. Features, in order, and how to validate each

Every feature the product has, in the order a document and a user meet them. Each one says what to do, what you should see, and which automated tests cover it. Run the stack first (step 2a).

**Samples.** Use made-up documents only, and keep them in `samples/`, which git ignores. The checks below make most of what they need themselves.

**Shortcuts** for a bash shell (Git Bash or WSL), run once from the repository root:

```bash
docker compose exec -T -e KAAGAZ_DEMO_PASSWORD=demo-pass-2026 api python -m kaagaz.cli seed-demo
API=http://localhost:8000/api; H='X-Requested-With: kaagaz'; J='Content-Type: application/json'
login() { curl -s -c "$1.jar" -H "$H" -H "$J" -d "{\"email\":\"$1\",\"password\":\"demo-pass-2026\"}" $API/auth/login; echo; }
up() { curl -s -b admin@sharma-traders.example.jar -H "$H" -F "file=@$1" -w ' HTTP %{http_code}\n' $API/documents; }
SQL() { docker compose exec -T postgres psql -U kaagaz -d kaagaz -c "$1"; }
printf 'Party Name,Profit,Date\nVerma Traders,50000,15/08/2026\nGupta Traders,72000,20/08/2026\nVerma Traders,30000,25/08/2026\n' > p.csv
requeue() { docker compose exec -T worker python -c 'import sys; from kaagaz.settings import Settings; from kaagaz.adapters.queue import SqsQueue; from kaagaz.ingestion.ports import Job; SqsQueue(Settings.from_env()).enqueue(Job(sys.argv[1], sys.argv[2]))' "$1" "$2"; }
login admin@sharma-traders.example
```

The demo users are `admin@`, `reviewer@` and `member@` on `sharma-traders.example`, and the same three on `mehta-textiles.example`, all with the password above. Each `login` saves a cookie jar named after the e-mail. Every POST, PATCH and DELETE needs the `X-Requested-With: kaagaz` header, or the API answers `403 csrf`. `SQL` runs as the table owner, so it sees every customer's rows; the app only ever sees one customer's.

### Signing in and staying safe

**1. Health check**
- Check: `curl -i $API/health`, then `docker compose stop elasticmq` and run it again.
- Expect: `200 {"status":"ok","database":"ok","storage":"ok","queue":"ok"}`; with the queue stopped, `503` with `"status":"degraded","queue":"error"`.
- Tests: `test_api_health.py`.

**2. Sign-in, who am I, sign-out**
- Check (API): `login admin@sharma-traders.example`, then `curl -b admin@sharma-traders.example.jar $API/auth/me`, then the same `login` with a wrong password.
- Expect: `200` with `{"user":{id,email,display_name,role},"customer":{id,name}}` and a `kaagaz_session` cookie (HttpOnly, 12 hours). A wrong password gives `401 bad_credentials`, the same reply for every kind of bad sign-in. Sign-out (`POST $API/auth/logout`) gives `204`; after it, `/auth/me` gives `401 not_signed_in`.
- Check (screen): open http://localhost:3000; it goes to /login. Submit empty, then type `admin` without a domain, then a wrong password.
- Expect: "Enter your e-mail address.", then "Enter an e-mail address with an @...", then the banner "The e-mail or password is not right." with your typing kept. **Show** / **Hide** reveals the password; it is hidden again on submit and when you leave the tab. Success opens /documents.
- Tests: `test_api_auth.py`; `frontend/tests/login-sign-in.test.ts`, `ui-password-field.test.ts`.

**3. Sessions, roles, CSRF and rate limit**
- Check: as admin, disable the member (feature 23), then use the member's jar on `/auth/me`. As the member, open `$API/review` and `$API/admin/users`. Send `POST $API/auth/logout` without the `X-Requested-With` header. Send 130 quick requests: `for i in $(seq 130); do curl -s -o /dev/null -w '%{http_code}\n' $API/health; done | sort | uniq -c`.
- Expect: the disabled member gets `401` at once. Members get `403 forbidden` on review and admin. No header gives `403 csrf`. About 120 replies of `200`, then `429 rate_limited` with `Retry-After: 60`. Every reply has `X-Request-ID`, `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.
- Tests: `test_api_auth.py` (`test_roles_and_the_admin_session_gate_the_routes`, `test_post_without_csrf_header_is_refused`, `test_rate_limit_gives_429_with_the_error_shape`, `test_disabling_a_user_ends_their_session_on_the_next_request`).

**4. App shell, navigation by role, theme**
- Check (screen): sign in as each role; narrow the window below 1024 px; pick Dark in the Theme menu and reload.
- Expect: a member sees Documents and Ask; a reviewer adds Review; an admin adds Review and Admin. Below 1024 px a menu drawer opens and closes with Esc or a link. Dark stays after a reload with no flash (cookie `kaagaz_theme=dark`). A member typing /admin sees "Admin is for admins".
- Tests: `frontend/tests/shell-nav.test.ts`, `ui-drawer.test.ts`, `theme.test.ts`.

### From upload to stored text

**5. Upload**
- Check: `up p.csv`, then `SQL "SELECT id,status,detected_type,size_bytes,display_name FROM documents ORDER BY created_at DESC LIMIT 1"`.
- Expect: `HTTP 201` with `"duplicate": false`; the row has type `text`, the file's size and name `p.csv`, and moves from `received` to `clean` to `ready` within a minute. With `docker compose stop elasticmq`, an upload fails and adds no row (the upload is undone).
- Check (screen): drag p.csv onto Documents.
- Expect: a progress bar, then "Uploaded" with an Open link.
- Tests: `test_upload_service.py`, `test_api_documents.py` (`test_upload_stores_queues_and_returns_the_summary`); `frontend/tests/shell-documents.test.ts`.

**6. File types and upload limits**
- Check: `: > e.csv; up e.csv`; `printf 'MZ\x90\x00' > a.exe; up a.exe`; any Word file `up letter.docx`; `head -c 52500000 /dev/zero > big.bin; up big.bin`.
- Expect: empty file `400 empty_file`; unknown type `400 unsupported_type`; a .docx is a zip, so it is accepted (`201`) and ends `needs_reader`; the big file `413 too_large`. The type always comes from the first bytes, never the name. An admin's lower limit (feature 24) also applies. Each type has its own limit (`MAX_UPLOAD_MB` for PDFs, `MAX_IMAGE_MB` for photos and scans, `MAX_SHEET_MB` for spreadsheets and CSV), and the 413 names the limit, plus the size when the file is under the largest limit (`big.bin` is over it, so its reply names only the limit). The screen refuses a .exe, an empty file, one over its type's limit, or a PDF or TIFF over 100 pages ("This PDF has 180 pages. The limit is 100.") before sending anything; its limits come from `GET $API/upload-limits`.
- Tests: `test_sniff.py`, `test_upload_service.py`, `test_api_documents.py` (`test_the_customers_own_limit_applies`).

**7. Fingerprint and duplicates**
- Check: `sha256sum p.csv` against `SQL "SELECT sha256 FROM documents WHERE display_name='p.csv'"`; then `up p.csv` again; then upload it as `admin@mehta-textiles.example`.
- Expect: the fingerprints match. The second upload gives `HTTP 200`, `"duplicate": true` and the same id, with still one row. The other customer gets `201` and a new id: duplicates are per customer. The screen says "Already uploaded".
- Tests: `test_spool.py`, `test_upload_service.py` (`test_same_file_twice_is_stored_once_and_says_duplicate`).

**8. Virus scan**
- Check: make the harmless EICAR test file `eicar.txt` (its text is on the EICAR website; your own antivirus may grab it) and `up eicar.txt`. Then stop `clamav` and upload another new file.
- Expect: `SQL "SELECT status,failure_code FROM documents WHERE display_name='eicar.txt'"` shows `rejected | virus_found`; the screen says "This file failed the virus check and was not processed."; `GET $API/documents/<id>/file` gives `404`; rejected is final. With the scanner down, the worker logs `"job retry"` with code `scanner_unavailable`, and the file stays `received` without being opened. Each try shows in the list ("Try 2 of 5"); after the 5th (about 25 minutes on, with the 5-minute lease) the file ends `failed | retries_exhausted`, saying "We tried 5 times and couldn't read this file."
- Tests: `test_scan_gate.py`, `test_adapter_scanner.py`, `test_pipeline.py` (`test_an_infected_file_stays_rejected_and_keeps_its_reason`).

**9. Page preparation and page pictures**
- Check: upload a two-page PDF, then `SQL "SELECT page_no,width,height,unit,has_image FROM pages WHERE document_id='<id>' ORDER BY page_no"` and `curl -b admin@sharma-traders.example.jar -o p1.webp $API/documents/<id>/pages/1/image`.
- Expect: two rows in `pt` (about 595 by 842 for A4) with `has_image = t`, and a WebP at 150 DPI. A spreadsheet gives one page per sheet in `cells` with no image; a photo gives `px`. With `MAX_PAGES=1` in `.env` and the worker restarted, a two-page PDF ends `failed | too_many_pages`.
- Tests: `test_preparing.py`, `test_api_documents.py` (`test_page_image`).

**10. Readers**

| Reader | Check | Expect |
|---|---|---|
| CSV | p.csv, then `SQL "SELECT role,reader,jsonb_array_length(output) FROM reader_outputs WHERE document_id='<id>'"` | `first | csv | 12` (one span per cell). A non-UTF-8 file ends `failed | not_utf8`; `printf '"abc\n' > bad.csv` ends `failed | malformed_csv` |
| Excel | an .xlsx with a header row | `ready`, reader `xlsx`; a macro file (.xlsm) ends `failed | macros_not_allowed` |
| PDF text | a digital PDF with "Invoice No: INV-17" and "Grand Total Rs. 1,180.00" | reader `pdfplumber`; a password-protected PDF ends `failed | encrypted` |
| Second PDF reader | the same PDF | a second `reader_outputs` row, `second | pdfium`; matching values are `verified` with reason `readers_agree` |
| OCR (scans, photos) | a photo of a printed invoice | reader `rapidocr`; values have a confidence and are `needs_review` (`single_reader` or `low_confidence`). A blank image ends `failed | no_text`; with `OCR_ENGINE=none` a photo ends `needs_reader` |
| Vision second reading | `MODEL_ENABLED=true` and `MODEL_VISION` set, then the photo again | scanned values get a `second_reading`; with Ollama stopped, the log says "vision reading skipped" and the document still ends `ready` |

- Tests: `test_csv_reader.py`, `test_xlsx_reader.py`, `test_pdf_reader.py`, `test_pdf_second_reader.py`, `test_ocr.py`, `test_image_reader.py`, `test_reader_socket.py`, `test_pipeline_scans.py`.

**11. Text pieces and meaning vectors**
- Check: `SQL "SELECT seq,page_from,label,text,regions FROM pieces WHERE document_id='<p.csv id>' ORDER BY seq"`.
- Expect: one piece per row, like `A: Verma Traders | B: 50000 | C: 15/08/2026`, each with `regions` pointing at its cells and label `verified`. PDF pieces join lines on a page and point at boxes. With `MODEL_ENABLED=true`, every piece gets an `embedding_model`; with the model down, the log says "pieces saved without meaning vectors" and search still works by words.
- Tests: `test_chunking.py`, `test_chunking_lines.py`, `test_indexing_db.py`.

**12. Extracted values and labels**
- Check: `SQL "SELECT record_key,field,value_text,value_number,value_date,label,label_reason FROM extracted_values WHERE document_id='<p.csv id>' ORDER BY record_key,field"`.
- Expect: one record per data row (`sheet:1:row:2` and so on) with `party_name`, `profit` (a number) and `date` (a real date), all `verified | direct_cell`. A PDF invoice gives `invoice_number`, `invoice_date`, `total_amount` and friends. Identity numbers such as a PAN are kept exactly as printed. Every value has a page and a region.
- Tests: `test_extraction_spreadsheet.py`, `test_extraction_labelled.py`, `test_extraction_labels.py`, `test_extraction_parsing.py`, `test_pipeline.py` (`test_identity_numbers_are_kept_exactly_as_read`).

**13. Safe reruns, retries and the failure bin**
- Check: note the piece and value counts of a ready document, run `requeue <customer_id> <document_id>`, then count again; `up bad.csv` from feature 10; stop the worker with `docker compose stop worker`.
- Expect: the same counts (a rerun replaces, never adds). The broken file ends `failed | malformed_csv` and its job goes to `kaagaz-jobs-failed` with that code; one bad file never blocks the next. Stopping the worker logs "stop requested, finishing the current job".
- Tests: `test_pipeline.py`, `test_worker.py`, `test_worker_main.py`, `test_adapter_queue.py`, `test_pipeline_store_db.py`.

### Working with documents

**14. Documents list, detail, original file, delete**
- Check (API): `curl -b admin@sharma-traders.example.jar "$API/documents?limit=10&q=p.c"`; `$API/documents/<id>`; `curl -i ... $API/documents/<id>/file`; `curl -i -b <jar> -H "$H" -X DELETE $API/documents/<id>`.
- Expect: the list is newest first with a `total`; the detail has `pages` and `values`; the file comes back with `Content-Security-Policy: sandbox` and is never served before it passes the scan; delete gives `204`, removes pages, pieces and values, and a member can delete only their own uploads (`403` otherwise).
- Check (screen): Documents shows name, status chip, type, size, uploaded and by; chips move from Checking to Reading to Ready by themselves (a request every 3 seconds while anything is in progress); search and paging work; Delete asks "Delete this document for everyone?".
- Tests: `test_api_documents.py`; `frontend/tests/shell-documents.test.ts`, `format.test.ts`.

**15. Document viewer**
- Check (screen): open a PDF, click a value in "Values found"; open p.csv.
- Expect: the viewer jumps to the value's page and draws a highlight box on it; zoom works from 50% to 300%. A spreadsheet shows a cell grid with the value's cell highlighted, 200 rows at a time. Page images and cells come from `/documents/<id>/pages/<n>/image` and `/cells`.
- Tests: `test_api_documents.py` (`test_sheet_cells_come_from_the_first_reader_output`); `frontend/tests/viewer-logic.test.ts`, `regions.test.ts`.

**16. Review queue and fixing a value**
- Check: make one doubtful value, `SQL "UPDATE extracted_values SET label='needs_review', label_reason='single_reader' WHERE id=(SELECT id FROM extracted_values LIMIT 1) RETURNING id"`; `login reviewer@sharma-traders.example`; `curl -b reviewer@sharma-traders.example.jar $API/review`; then `curl -b reviewer@sharma-traders.example.jar -H "$H" -H "$J" -d '{"choice":"typed","value":"55000","remember":true}' $API/review/<value id>`.
- Expect: the queue lists only `needs_review` values, oldest first, each with its page. The fix gives `200`; the value becomes `verified | corrected`; `SQL "SELECT choice,old_value,new_value FROM corrections"` and `SQL "SELECT field,raw_value,corrected_value FROM learned_corrections"` each gain a row. Choosing `second` when there is no second reading gives `400`. Sending the same fix again, or fixing a value that is already `verified`, gives `409 not_needs_review` and changes nothing. On screen, the card leaves and the count drops.
- Tests: `test_api_review.py`; `frontend/tests/review-queue.test.ts`.

**17. Remembered corrections**
- Check: after feature 16 with `remember: true`, upload another file where the same field reads the same raw text.
- Expect: the new value shows the corrected text with `verified | learned_correction`, for this customer only.
- Tests: `test_extraction_labels.py`, `test_pipeline_db.py`.

### Asking questions

**18. Chat: a question to a cited answer**
- Check: with p.csv ready, `curl -b admin@sharma-traders.example.jar -H "$H" -H "$J" -d '{"question":"Kiska profit sabse jyada tha last month?"}' $API/chat` (the example assumes today is in September 2026).
- Expect: `route:"fixed_query"`, the sentence "Verma Traders had the highest profit, 80,000, in August 2026.", citations pointing at the cells, `label:"verified"` and `model_used:false`. Hinglish is turned into English (`normalized_question`), and "last month" is worked out in `TIMEZONE`. Numbers always come from fixed queries, never from SQL a model wrote.
- Tests: `test_answering_golden.py` (`test_the_hinglish_example_is_answered_by_a_fixed_query`), `test_answering_hinglish.py`, `test_answering_dates.py`, `test_answering_intent.py`, `test_answering_fixed_queries_db.py`.

**19. Search, "not found" and the claim check**
- Check: ask a question that is not about a number, with a typo in a word from a document; then ask "What is the weather in Delhi?".
- Expect: the first gives `route:"search"` with `kind:"piece"` citations (exact words, typo-tolerant and, with a model, meaning search are merged). The second gives `status:"not_found"`, the text "I couldn't find this in your documents." and a list of closest documents. With a model on, any sentence citing nothing, or with a number that is not in its evidence, is dropped, and links and markup are stripped.
- Tests: `test_search_db.py`, `test_search_fuse.py`, `test_answering_golden.py` (`test_ten_unanswerable_questions_are_all_not_found`, `test_prompt_injection_in_evidence_changes_nothing`), `test_answering_verify.py`, `test_answering_sanitize.py`.

**20. Answer labels**
- Check: `SQL "UPDATE extracted_values SET label='needs_review', label_reason='single_reader' WHERE field='profit' AND value_text='50000'"`, then ask feature 18's question again.
- Expect: `label:"needs_review"`: an answer is verified only when everything behind it is. On screen the badge reads "Needs review" and the source link says "Check page N".
- Tests: `test_answering_golden.py` (`test_one_unverified_value_makes_the_answer_needs_review`), `test_answering_fixed_queries_db.py`.

**21. Conversations and feedback**
- Check: `curl -b <jar> $API/conversations`, then `$API/conversations/<id>`; ask again with `"conversation_id":"<id>"`; `curl -i -b <jar> -H "$H" -H "$J" -d '{"rating":"down","reason":"wrong total"}' $API/messages/<answer message id>/feedback`.
- Expect: conversations list newest first and belong to their user only (a colleague gets `404`); the follow-up lands in the same conversation; feedback gives `204`, a second vote replaces the first, and the reason is stored as typed. On screen the URL becomes `/chat?c=<id>` and a reload brings the conversation back.
- Tests: `test_api_chat.py`; `frontend/tests/chat-logic.test.ts`, `chat-answer.test.ts`.

**22. Answers with a model**
- Check: pull `MODEL_CHAT` and `MODEL_EMBED` into Ollama, set `MODEL_ENABLED=true`, restart `api` and `worker`, and ask a search question.
- Expect: `model_used:true` and cited sentences. Stop Ollama: answers fall back to quoting lines from the evidence, and nothing fails.
- Tests: `test_answering_golden.py` (`test_a_model_answer_is_used_and_cited`, `test_a_failed_model_falls_back_to_quoting`), `test_ai_client.py`.

### Administration and safety

**23. Admin: users**
- Check: `curl -b admin@sharma-traders.example.jar $API/admin/users`; add one with `-H "$H" -H "$J" -d '{"email":"new@sharma-traders.example","display_name":"New","role":"member","password":"long-password-1"}' $API/admin/users`; change a role or disable with `PATCH $API/admin/users/<id>`.
- Expect: `200` list; `201` for the new user, who can sign in; `409` for an e-mail already in use; `400` if an admin tries to disable themselves or drop their own admin role. On screen, "Add a user" names each problem against its field.
- Tests: `test_api_admin.py`; `frontend/tests/shell-admin.test.ts`.

**24. Admin: upload limit**
- Check: `curl -b <admin jar> $API/admin/settings`, then `PATCH` it with `{"max_upload_mb":5}` and upload a 6 MB file.
- Expect: the platform limit of 50 is shown; the new limit saves; the 6 MB upload gets `413`; a limit above 50 gives `400`.
- Tests: `test_api_admin.py` (`test_settings_read_and_change_within_the_platform_limit`), `test_api_documents.py`.

**25. Admin: activity and numbers**
- Check: `curl -b <admin jar> "$API/admin/audit?limit=10"` and `$API/admin/stats`.
- Expect: the audit log newest first, with names and actions such as `auth.login`, `document.upload`, `chat.ask`, `review.resolve`, `user.create`; the stats count documents by status, questions, answers found and not found, feedback and open review items, for this customer only.
- Tests: `test_api_admin.py`.

**26. Customer separation**
- Check: `login admin@mehta-textiles.example`, then use its jar to `GET $API/documents/<a Sharma document id>`, `POST $API/review/<a Sharma value id>` and `PATCH $API/admin/users/<a Sharma user id>`; in the database, `SQL "BEGIN; SET LOCAL ROLE kaagaz_app; SELECT count(*) FROM documents; COMMIT;"`.
- Expect: `404 not_found` every time, never someone else's data. The app's own database role sees 0 rows until a customer is set for the transaction.
- Tests: all `test_isolation_*.py` files (the `isolation` marker).

**27. Demo data and command-line tools**
- Check: run the seed command twice; `docker compose exec -T -e KAAGAZ_ADMIN_PASSWORD=long-password-1 api python -m kaagaz.cli create-customer --name Test --admin-email a@test.example --admin-name A`.
- Expect: the first seed prints both customers and their e-mails, the second prints "already there, skipped"; `create-customer` prints `customer <uuid>`. A missing or short password stops with `error: ...` and exit code 2.
- Tests: `test_cli.py`.
- Measure (PRD 2 X4): from `backend/` with `.env` loaded, `python -m kaagaz.cli measure --file <a local file> [--truth <folder of 1.txt, 2.txt, ...>]`. It prints each page's route and why, the route check's numbers and the seconds, then the totals; no text and no file name. It uses no database, storage or queue, and it skips the virus scan, so use it only on files you trust. Tests: `test_cli_measure.py`.

**28. Settings checks at start-up**
- Check: `docker compose run --rm -e SESSION_SECRET=short worker`, and again with `-e OCR_ENGINE=x`.
- Expect: the container stops at start-up naming the problem: "SESSION_SECRET must be at least 32 characters", "OCR_ENGINE must be one of ['none', 'rapidocr']".
- Tests: none yet for reading settings from the environment; the adapters' own limits are covered in `test_adapter_queue.py` and `test_adapter_scanner.py`.

### Sign-off

| # | Feature | Pass / fail | Notes |
|---|---|---|---|
| 1-4 | Health, sign-in, sessions, roles, app shell | | |
| 5-9 | Upload, types and limits, duplicates, virus scan, page pictures | | |
| 10-13 | Readers, pieces, values, reruns and failures | | |
| 14-17 | Documents, viewer, review, remembered corrections | | |
| 18-22 | Chat, search, labels, conversations, model answers | | |
| 23-28 | Admin, customer separation, tools, settings | | |
