# How the upload journey works

> DRAFT (#31). Describes local, unmerged code on `local/s1-foundation`. Parts
> marked PROVISIONAL wait for Vrushit's decisions.

A customer uploads a file. Here is everything that happens to it, in order,
and what happens when a step fails.

## 1. Upload

The file is read once. While reading, it is fingerprinted (a short code worked
out from the file's bytes, SHA-256), counted, and copied to a temporary file.

| If | Then |
|---|---|
| It is empty | Refused: `empty_file`. Nothing kept. |
| It is over the size limit | Reading stops one byte past the limit. Refused: `too_large`. Nothing kept. |
| Its first bytes are not a known type (PDF, PNG, JPEG, TIFF, zip, old Office, plain text), or the type is not on the allowed list | Refused: `unsupported_type`. The file name and what the browser says are never trusted. |
| This customer already uploaded the same bytes | Reply "duplicate" with the existing document. Nothing new stored. Other customers are never checked (PROVISIONAL, PRD section 6). |

Otherwise the file is stored under the customer, recorded, and a job is put in
the queue. If storing, recording or queueing fails, the steps already done are
undone so the customer can simply try again. If the undo itself cannot reach
the backend, it stops so a record never points at a deleted file; the leftover
is listed in `backend/kaagaz/ingestion/service.py` and needs a clean-up job
later (PROVISIONAL).

## 2. Virus scan (first step of the job)

PROVISIONAL: where the scan runs is PRD question 4.

| If | Then |
|---|---|
| The scanner says clean | The document is marked clean. |
| Infected, cannot be scanned, or any unclear answer | Marked rejected for good, the file is quarantined, the job ends: `virus_found` or `unscannable`. Any pieces from an earlier clean run are removed. |
| Already rejected (a job delivered again) | Refused at once: `rejected`. Never scanned or read again. |
| No record for this customer, or the file is missing | `document_missing`, failure bin at once. Nothing is read. |
| The scanner is down | The job is retried later. The file is never opened meanwhile. |

## 3. Read

The exact bytes that were scanned are read through the reader plug socket. They
must still match the upload's fingerprint (`content_changed` otherwise).

| File | Reader | If it fails |
|---|---|---|
| Any plain UTF-8 text file | CSV reader: every cell with row and column. PROVISIONAL: .txt, HTML and JSON are also read as CSV until Vrushit decides (`backend/kaagaz/ingestion/sniff.py`) | `not_utf8`, `not_text`, `malformed_csv`, `too_many_cells` |
| Excel .xlsx | Excel reader: every cell with sheet, row and column. PROVISIONAL: a standard-library reader pending Vrushit's Excel library decision; its limits and refusals are his security settings | `corrupt_file`, `too_large_inside`, `too_much_text`, `too_many_cells`, `too_many_parts`, `duplicate_parts`, `encrypted`, `unsupported_compression`, `doctype_not_allowed`, `external_link`, `macros_not_allowed`, `unsupported_spreadsheet` |
| PDF, photos, Word, old Office | None yet | Not a failure: marked `needs_reader` until Sprint 2 or file preparation (#17) |

## 4. Mask, split, save

Aadhaar and PAN numbers written in the forms listed in
`backend/kaagaz/masking/identity.py` are masked in every value before the text
is saved as pieces (PROVISIONAL rule, PRD question 1). Not masked yet: a number
split across two cells, and full-width look-alike characters. The uploaded file
itself is stored as it arrived. The text is split into one piece per row, each
piece remembers every cell it came from, and the document is marked ready. Just
before saving, the document must still be clean (`not_scanned` otherwise).

## When a job keeps failing

A job that fails for a reason a retry could fix (for example the scanner is
down) is tried again after a waiting time (a queue setting for Vrushit). After
the maximum number of tries it moves to the failure bin marked
`attempts_exhausted`; the reason for the last try is not kept yet (PROVISIONAL).
An unexpected error, such as saving pieces failing, is retried the same way. A failure a retry cannot fix (a virus, a broken file) goes to the
failure bin at once. One bad file never blocks the files behind it.

## Not built yet

The upload endpoint itself (there is no web layer yet), file preparation
(#17), page pictures (#18), PDF reading, the upload screen (#22), the real
storage, database, queue and scanner, and the wiring that puts them together.
See `backend/README.md`.
