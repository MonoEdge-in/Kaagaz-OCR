# UI mapping: Kaagaz UI handoff → backend

Step 0 of `kaagaz-ui-handoff/HANDOFF.md`. For every screen in the handoff, what it shows, and the endpoint and field that provides it.

- **Baseline:** branch `feature/UI-UX` = `production` at `90aa054` (File Management, #209), migrations up to `0042`.
- **Handoff location:** `design/kaagaz-ui-handoff/` (the updated zip of 20 Oct 2026, D-1).
- **All API paths** are under `/api`, proxied by Next to the backend.
- **CSRF:** every non-GET request sends `X-Requested-With: kaagaz`.
- **Errors** come back as `{error:{code,message,details?}}`.

**How each row is marked:**
- **Yes:** the backend provides it today.
- **Client:** built in the browser from data the backend already gives.
- **Partial:** close, but a field or filter is missing; the gap is written next to it.
- **Needs backend:** not available. The UI leaves it out, behind a marked `TODO(needs-backend: NB-xx)`, and never shows made-up data. Numbered in section 4.

---

## 1. What the frontend is today (and what the handoff changes)

| Topic | Today (`frontend/`) | Handoff | Plan |
|---|---|---|---|
| Framework | Next 16, React 19, App Router, `pdfjs-dist`; Vitest (node env) with 89 test files; no Playwright | Keep framework | Keep |
| Styling | Plain CSS: `app/globals.css` (tokens `--bg-page`, `--accent`… light + dark) + 27 `*.module.css` | Tokens `--k-*` in `app/styles/tokens.css`, `base.css`, CSS Modules per component | Add `tokens.css`/`base.css`; move components over screen by screen; delete old tokens when nothing uses them |
| Hex rule | `tests/guards.test.ts:32` allows hex only in `globals.css` | Hex only in `tokens.css` | Add `app/styles/tokens.css` to the allowed list (one-line test change, called out in the PR) |
| Theme | Cookie `kaagaz_theme` = light/dark/system (`lib/theme.ts:184`), read on the server in `app/layout.tsx:49-53`, so no flash; "system" sets no attribute and CSS follows `prefers-color-scheme` | `data-theme` on `<html>`, light/dark/system, saved per user, no flash | Keep the cookie (already flash-free). The handoff's dark block is `[data-theme="dark"]` only: also emit it under `@media (prefers-color-scheme: dark) :root:not([data-theme="light"])` so System keeps working. "Saved per user" = **NB-01** |
| Fonts | `next/font/google`: Inter, JetBrains Mono, Archivo | Archivo (wdth 100–125), IBM Plex Sans, IBM Plex Sans Devanagari, IBM Plex Mono | Swap in Step 1. Self-hosted, so the report-only CSP (`font-src 'self'`) is fine |
| Icons | `components/ui/Icon.tsx` + `icons.ts` | Paths from `Main.html`, 24 grid, stroke 1.6 (DevNotes says 1.75) | New set in `components/ui/Icon/` at 1.6 (D-3), plus ten icons the screens draw but `Main.html` leaves out; the old set stays until each screen moves over |
| Auth gate | Client only: `SessionProvider` calls `GET /auth/me` (`lib/session.tsx`), no middleware | Same screens | Keep; restyle |
| Routes | `/` redirects to `/documents`; `/documents`, `/documents/[id]`, `/chat` (Ask), `/review`, `/words`, `/admin`, `/settings/ai`, `/login`, `/signup`, `/operator`, `/operator/login` | Adds Home, Search and an Upload page | New `/home` (and `/` goes there), new `/search`. No `/upload` page (D-12) |
| Upload | Drop zone and batch tray on Documents | A separate Upload page | A drop zone plus a batch tray visible on every page; the top-bar Upload button opens the file picker and the tray (D-12) |
| Status | `STATUS_CHIPS` in `lib/format.ts:161-168` | One mapping in `lib/status.ts`, six states | Section 2 |
| Nav | Documents, Ask, My words, Review (reviewer/admin), Admin (admin + admin session) | Home, Documents, Ask, Review (with count), My words, Admin | Review stays reviewer/admin only: `GET /review` is reviewer-only (decision D-7) |

---

## 2. Status mapping (`lib/status.ts`)

The handoff's six states, and which backend values feed each. This is the only place this mapping lives.

| UI state | Backend values |
|---|---|
| **Verified** (green, check) | value `label = verified`; answer `status = answered` with `label = verified` |
| **Needs review** (dashed amber) | value `label = needs_review` (reason text from `label_reason`, below); answer `label = needs_review`; answer `status = unchecked` (figure only on a card: "needs confirming in Review"); page `read_quality = uncertain` |
| **From documents** (soft blue) | answer `status = answered` with `label = null` (a search answer with no checked figure) |
| **Not found** (neutral) | answer `status = not_found` (plus `unreadable_pages`, `closest_documents`) |
| **Reading** (calm animation) | document `status = received` or `clean`; `progress.step` in `scanning, preparing, reading, rereading, understanding, indexing, waiting`; text from `status_message` |
| **Failed** (red, reason + fix) | document `status = failed` (`failure_code`: `too_many_pages, retries_exhausted, unreadable, ocr_failed, customer_disabled`), `rejected` (`virus_found, unscannable, blocked_content`), `needs_reader`; page `read_quality = unreadable` with `problems[{code,says,fix}]` |

- **Reason and fix text.** For pages and the first unreadable page, use the server's `says` and `fix`. For document failures, use `status_message`, plus a fix line kept per `failure_code` in `lib/status.ts`.
- **Value reason text, in plain words, from `label_reason`:**
  - `readers_disagree`: "Two readers read this differently"
  - `unsure_text`, `low_confidence`, `ocr_script`: "Hard to read"
  - `box_unanchored`: "The exact spot isn't confirmed"
  - `check_failed`: "The totals don't add up"
  - `local_reader`: "Read by the backup reader"
  - `single_reader`: "Only one reader saw this"
  - `model_chosen`: "Picked by Kaagaz, check the page"
  - `corrected` and `learned_correction`: Verified, "Fixed by a reviewer"
- **Sensitivity labels:** `public | internal | confidential | restricted` (default `internal`) → LabelChip.

---

## 3. Screen by screen

### 3.1 Shell (all app screens)

| Element | Source | Status |
|---|---|---|
| Org name, user name, role | `GET /auth/me` → `customer.name`, `user.display_name`, `user.role` | Yes |
| Admin item | `role = admin` and `session.scope = admin` | Yes |
| Review count badge | `GET /review?limit=1` → `total` (reviewer/admin only) | Yes |
| Search pill → Search | `/search?q=` (new route) | Client |
| Upload button, global drop | Upload tray (3.4) | Client |
| Bell + unread count, notifications | none | **Needs backend NB-02** |
| Account menu: theme | cookie (1) | Yes |
| Account menu: Your profile | only `POST /auth/password` exists | Partial: "Change password" only |
| Sign out, Sign out everywhere | `POST /auth/logout`, `POST /auth/logout-everywhere` | Yes |
| Phone tab bar, More | same routes | Client |

### 3.2 Sign in (`desktop/SignIn.html`)

| Element | Source | Status |
|---|---|---|
| Sign in | `POST /auth/login {email,password}` → `Me` + cookie; wrong details → 401 `bad_credentials` | Yes |
| Admin 6-digit code | 401 `code_required`, then `POST /auth/login {email,password,code}` | Yes |
| Two-step setup (QR, key, code) | 403 `mfa_enrol_required` `details{enrol_token,secret,otpauth_uri}` → QR drawn from `otpauth_uri` → `POST /auth/admin/enrol {enrol_token,code}` | Yes (the repo already has a QR encoder) |
| "Next: save 8 backup codes", "Use a backup code" | none | **Needs backend NB-03** (also no screen designed) |
| Forced new password | `Me.user.must_change_password` → `POST /auth/password {current_password,new_password}`; rules: 12+ characters checked in the browser, the rest from the 400 reply | Yes |
| Locked screen (unlock time, admin name), "3 tries left" | Backend returns the same 401 for locked and wrong password, on purpose (no account probing) | **Won't build** (decision D-6) |
| Forgot password, Reset my password | none (admins reset passwords) | **Needs backend NB-04**; until then: "Ask your admin to reset it" |
| "Keep me signed in" | session length is set by the server (idle timeout, 0040) | **Needs backend NB-05**; leave out |
| Sign up (not in handoff) | `GET/POST /auth/signup` | Yes; restyle in the same system |
| Operator sign-in | `POST /operator/login {email,password,code}` | Yes |

### 3.3 Home (`desktop/Home.html`, `phone/PhoneHome.html`)

| Element | Source | Status |
|---|---|---|
| Greeting, name, date, org | `/auth/me`; date in the browser | Yes |
| "N versions and M values are waiting" | `dashboard.tiles.versions_to_confirm`; values = `GET /review` total (reviewer/admin) | Partial: members get no values count |
| Ask box, suggestion chips → Ask | `/chat?q=`; chips from today's starter logic (`components/chat/logic.ts:346-377`) | Client |
| Review panel count | `GET /review?limit=1` total | Yes |
| Review panel "9 in Land & Legal, 14 in Accounts" | none (no per-department review count) | **Needs backend NB-06** |
| Tiles: Documents (+this week), Departments, New versions this month, Versions to confirm | `GET /dashboard` → `tiles{documents,documents_this_week,departments,new_versions_this_month,versions_to_confirm}` | Yes |
| "29 linked by you, 8 on their own" | none (no manual vs auto split) | **Needs backend NB-07** |
| Documents by type | `dashboard.by_type[{doc_type,count}]` | Yes |
| Department cards, file counts | `dashboard.departments[{id,name,sort_order,files}]` | Yes |
| Department "Updated today" | none | **Needs backend NB-08** |
| "Not in a department: N" | `dashboard.not_in_department` | Yes |
| Recent documents: name, label, version, when | `dashboard.recent[{id,display_name,doc_type,sensitivity,version_no,status,created_at}]` | Yes |
| Recent documents: folder path | not in `recent` | **Needs backend NB-09** (add `folder{path}`) |
| Recent activity | `dashboard.activity[AuditEntry]` (`actor_name, action, document{name}, folder{path}, details, created_at`) | Yes; words from one `activity/words.ts` table |
| Phone "Continue where you left" (recently viewed by me) | none for members (views are in the audit log, admin only) | **Needs backend NB-10** |

### 3.4 Upload + batch tray (`desktop/Upload.html`, `phone/PhoneUpload.html`)

No separate Upload page (D-12): a drop zone, plus a batch tray visible on every page. The top-bar Upload button opens the file picker and the tray.

| Element | Source | Status |
|---|---|---|
| Limits in the drop text | `GET /upload-limits` → `pdf_bytes, image_bytes, sheet_bytes, max_pages` | Yes |
| "up to 100 files" | `MAX_FILES_PER_BATCH` is not exposed | **Needs backend NB-11** (small); 409 `batch_full` is handled meanwhile |
| "HEIC" in the accepted list | not an accepted type (PDF, PNG, JPEG, TIFF, XLSX, CSV) | Leave HEIC out |
| Take a photo (phone) | `<input type=file accept=image/* capture>` (the phone's own camera app; the Permissions-Policy camera block doesn't apply) | Client |
| Department picker, "Let Kaagaz decide" | `POST /documents` form `department_id`; leave it out to let Kaagaz decide | Yes |
| Batch | client `batch_id` uuid → `GET /batches/{id}` → `counts{total,ready,reading,waiting,attention,failed}, pages_read, pages_total, time_left, version_suggestion_count, version_suggestions[]` | Yes |
| Batch title, started by, "Into Land & Legal" | browser (time, `/auth/me`, chosen department) | Client |
| Stage 1 Sending (%) | XHR upload progress | Client |
| Stage 2 Saving | between the last byte and the 201/200 reply | Client |
| Stage 3 Reading "n of m pages" | `progress{step,done,total}`, `status_message`, `time_left` | Yes |
| Progress number | `0.2·sent + 0.2·saved + 0.6·(done/total)`, capped at 99 until `status = ready` | Client |
| Filed-to path | `DocumentSummary.folder_id` + the folder tree; `autofile = null` → "Filing after reading" | Yes |
| Re-filed reason ("moved out of Land & Legal, it's an invoice") | `autofile` is only `filed|not_filed` | **Needs backend NB-12** |
| "Looks like version 2 of X", Review N | `version_suggestions[]` (`text_overlap, name_similarity, shared_identifier, reasons`) | Yes |
| Duplicate "Already uploaded on … by … in …" | upload reply `{duplicate:true, in_trash, document}` → `uploaded_by_name, created_at, folder` | Yes |
| Duplicate "Keep both" | identical files are refused by fingerprint by design | **Won't build** (D-5); "Skip this file" and "Open it" |
| Failed: reason + fix | `failure_code`, `status_message`, `first_unreadable{page_no,says,fix}` | Yes |
| Failed: Upload sharper scan | `POST /documents/{id}/versions` | Yes |
| Failed: Retry | `POST /documents/{id}/reprocess` (reviewer/admin) | Partial: members don't see Retry |
| "You can leave this page", tray after reload | the server keeps reading; the tray keeps `batch_id` in the browser for 24 h | Client |
| Phone "We'll notify you when ready" | none | **Needs backend NB-02** |

### 3.5 Documents list and grid (`desktop/Documents.html`, `DocumentsGrid.html`, `phone/PhoneDocuments.html`)

| Element | Source | Status |
|---|---|---|
| Folder tree, Departments then Other | `GET /folders` → `{id,name,parent_id,kind,sort_order}` | Yes |
| Count next to each folder, subfolder card counts | none | **Needs backend NB-13** |
| "Not in a department" count | `dashboard.not_in_department` | Yes |
| Trash count | `GET /documents?trashed=true&limit=1` → `total` | Yes |
| Breadcrumbs | from the folder tree | Client |
| "filed here by Kaagaz from the survey number" | folder `kind` (`property`, `party`, `year`, `category`) | Client |
| New folder, Upload here | `POST /folders {name,parent_id}`; upload with `folder_id` | Yes |
| Subfolder subtitle in Marathi ("सातबारा") | none | **Needs backend NB-14**; leave out |
| Filters: Department, Owner, Label, Party, Property, Document date, Has newer version, Needs attention, Tags | `GET /documents` `department_id, owner_id (+GET /people), sensitivity[], party, property, doc_date_from/to, has_newer_version, attention, tag_id[]` | Yes |
| Filter: Type (document kind, e.g. 7/12) | the list has only `detected_type` (file type); `doc_types[]` exists on search only | **Needs backend NB-15** (small) |
| Sort: name, type, owner, label, version, document date, last updated | `sort` + `order` | Yes |
| Sort by "Updated by" | not a sort key | Leave that header unsortable |
| Bulk: Move, Tag, Read again, Trash | per document: `PATCH /documents/{id} {folder_id}`, `PUT …/tags`, `POST …/reprocess` (reviewer), `DELETE /documents/{id}` | Yes (one call per document, as today) |
| Row: name, type, owner, label, version, document date, last updated, updated by | `DocumentSummary`: `display_name, doc_type, owner{name}, sensitivity, version_no, document_date, updated_at, updated_by` | Yes |
| Row "v1 · newer?" | `pending_suggestion`, `newer_version` | Yes |
| Row problem "2 pages unreadable" | `pages_unreadable, pages_uncertain, values_to_review` | Yes |
| Row reading state "Reading 3 of 5" | `status`, `progress` | Yes |
| Row subtitle (type in Marathi) | from `doc_type` with a client label table | Client |
| Pagination "Load 3 more" | `limit`/`offset`, `total` | Yes |
| Grid thumbnail | `GET /documents/{id}/pages/1/image` (webp); overlay from `pages_unreadable`/`pages_uncertain` | Yes |
| Phone "Search in this folder" | `GET /search?q&folder_id&include_subfolders=true` | Yes |

### 3.6 Document page (`desktop/DocumentPage.html`, `DocumentTabs.html`, `phone/PhoneDocument.html`)

**Viewer, banners and header**

| Element | Source | Status |
|---|---|---|
| Pages, viewer | `GET /documents/{id}` (`page_count`, `pages[]`), PDF from `/file`, images from `/pages/{n}/image`, sheets from `/pages/{n}/cells` | Yes |
| Zoom, fit, prev/next | | Client |
| "Scanned 1 Oct 2026 · 300 dpi" | `created_at`, `pages[n].effective_dpi` | Yes |
| Page problem banner | `pages[n].read_quality`, `problems[{code,says,fix}]` | Yes |
| Page actions: Read this page again, Keep as is | `POST …/pages/{n}/read-again` (reviewer/admin; 409 if the page isn't a scan), `POST …/pages/{n}/accept` | Yes (reviewer/admin) |
| Page action: Upload a better scan | `POST …/versions` | Yes |
| Thumbnail flags "!" / "?" | `read_quality`; values with `label=needs_review` on that page | Yes |
| Marks: cited spot | `?page=&region=` from Ask/Search | Yes |
| Marks: unsure and unreadable words | `GET …/pages/{n}/readings` → `marks[{quality:unsure|illegible,region,text}]` | Yes |
| Marks: spot not confirmed | values with `label_reason=box_unanchored` (`region`) | Yes |
| Header: name, "Version N of M", label, pages, type, owner | `display_name, version_no, versions[], sensitivity, page_count, doc_type, owner` | Yes |
| Ask about this | `/chat?document={id}` | Yes |
| Download | `/file?download=1` (logged as a download) | Yes |
| More: rename, move, tags, trash | `PATCH`, `PUT …/tags`, `DELETE` | Yes |
| Version suggestion banner | `version_suggestion{text_overlap,name_similarity,shared_identifier,reasons,next_version_no,candidate}` → accept/reject (3.7) | Yes |
| Auto-linked banner + Undo | `versions[].joined = auto` → `POST …/version-undo` | Yes |

**Values tab**

| Element | Source | Status |
|---|---|---|
| Rows, "6 verified · 2 need review" | `values[]`: `field, value, raw, second_reading, page_no, region, label, label_reason, votes` | Yes |
| Bilingual field label | `field` key only | Client label table; Marathi labels are **NB-14** |
| Two readings shown | `value` / `second_reading` | Yes |
| Reason sentence ("Read from a Marathi stamp") | `label_reason` | Partial: generic reason text only (section 2) |
| Review link | `/review?document_id=` | Yes |

**Details tab**

| Element | Source | Status |
|---|---|---|
| Owner picker | `GET /people` + `PATCH {owner_id}` | Yes |
| Label picker | `PATCH {sensitivity}` | Yes |
| Department path + Change | `folder{path}` + move | Yes |
| Type, document date | `doc_type`, `document_date`; subtype from `card.title` | Yes |
| Author | `versions[0].uploaded_by`, `created_at` | Yes |
| Last updated | `updated_at`, `updated_by` | Yes |
| Pages + "1 page partly unreadable" | `page_count`, `pages_unreadable`, `pages_uncertain` | Yes |
| Party, property | `card.parties`, `card.identifiers` (`survey_number`) | Yes |
| Related documents + reason ("Same survey number") | `links[{id,display_name,doc_type,identifier{kind,value}}]` | Yes |
| Related documents' label | not in `links` | **NB-16** (small) |

**Text, Questions, Versions and Activity tabs**

| Element | Source | Status |
|---|---|---|
| Text: lines and marks | `GET …/clear-copy` → `pages[{page_no,lines,marks}]` | Yes |
| Text: language tag per page | document-level `languages` only | Partial |
| Text: find, copy | | Client |
| Questions | `GET …/questions` → `[{question,asked_at,answer}]` | Yes, but **only this person's own questions** (conversations are private); the handoff's "asked by Anita" for other people is decision D-8 |
| Versions list | `versions[{version_no,version_note,uploaded_by,created_at,is_current,joined}]` | Yes |
| Versions: View, Compare, Make current | `/documents/{vid}`, compare, `POST …/make-current` | Yes |
| Versions: "91% same text, same agreement number" | not stored on the version | **NB-17** (small) |
| Activity timeline | `GET …/activity?cursor` → `AuditEntry[]` | Yes |
| Activity filters All/Views/Changes/Downloads | filtered by `action` in the browser | Client |

### 3.7 New version + Compare (`desktop/VersionCompare.html`, `flows/FlowUpload.html`)

| Element | Source | Status |
|---|---|---|
| Suggestion banner, reasons | `version_suggestion` (3.6) | Yes |
| Make it version N | `POST /version-suggestions/{id}/accept {version_note?}` | Yes |
| Keep separate | `POST …/reject` | Yes |
| Compare first | `GET /documents/{id}/compare?with={candidate_id}` | Yes |
| Compare panes, changes, navigator, mini-map | `pages[{page_no,changes,runs[{kind:same|removed|added,text}]}], summary`; navigator and mini-map built from `runs` | Yes |
| Left/right version pickers | `versions[]` | Yes |
| Side by side / Text only | | Client |
| "What changed, in plain words" (English explanation per change) | none | **Needs backend NB-18** |
| Glossary glosses from My words | `GET /me/words`, matched in the browser | Client |
| Toast "Saved as version 2… Undo" | `POST …/version-undo` | Yes |

### 3.8 Ask (`desktop/Ask.html`, `AskStates.html`, `phone/PhoneAsk.html`, `flows/FlowAsk.html`)

**Conversations and scope**

| Element | Source | Status |
|---|---|---|
| Conversation list, Today / This week groups | `GET /conversations` (`created_at`) | Yes; grouped in the browser |
| Search conversations | titles, filtered in the browser | Client |
| Rename, Delete | `PATCH /conversations/{id} {title}`, `DELETE` | Yes |
| Copy all answers | | Client |
| Scope: All / A folder / Selected documents | `POST /chat` `scope:{folder_id,include_subfolders,document_ids[]}` | Yes |
| "38 documents" in scope | folder → `GET /documents?folder_id&include_subfolders&limit=1` total; selected → count in the browser | Yes |

**Answer**

| Element | Source | Status |
|---|---|---|
| Ask | `POST /chat {question,conversation_id?,scope?}` → `{conversation_id, question, answer}` (one JSON reply, **no streaming**) | Yes |
| Status | `answer.status`, `answer.label` → section 2 | Yes |
| Text with numbered citations | `sentences[{text,citations[n]}]`, `citations[]` | Yes |
| Meta "From 1 document, 2 versions · answered 11:05" | built from the citations and `created_at` | Client |
| Source card: crop of the spot | `/pages/{n}/image` + `regions` | Yes |
| Source card: document, page, label | `document_name`, `page_no`, `label` | Yes |
| Source card: version number | not on the citation | **NB-19** (small) |
| Open page N | `/documents/{id}?page=&region=` | Yes |
| "Searching for: …" | `rewritten_question` | Yes |
| "scope widened to All documents" | not returned | **NB-20** |
| Table answer (Party / Invoices / Total) and "Rows behind the total" | fixed-query answers return sentences and up to 20 value citations, no table | **Needs backend NB-21**. Until then the value citations as a plain list: no table, no Export to Excel |
| Export to Excel (answer) | needs NB-21 rows | **NB-21**; left out |
| Copy answer with quotes | | Client |
| Helpful? Yes/No | `POST /messages/{id}/feedback {rating}` | Yes |
| Pin source | `POST /messages/{id}/pin` | Yes |
| Needs-review warning in an answer | `answer.label = needs_review` | Yes |
| Needs-review detail ("₹1,50,000 / ₹1,30,000; the words say…") | citation has `ref_id` only | **NB-22** |

**Working states and not found**

| Element | Source | Status |
|---|---|---|
| **Live steps** ("Searching 120 invoices… Reading 12 pages…") | none: the reply is synchronous | **Needs backend NB-23** (stream steps). Until then one line, "Working on it", with the elapsed time; never a step that didn't happen |
| Stop | aborts the request in the browser (the server still finishes) | Client |
| Answer survives tab switch | the request is kept in an app-level store while the app stays open; tab title "(1) Answer ready" | Client |
| Answer after a full reload or close | none | **NB-23** |
| Not found + unreadable pages | `status=not_found`, `unreadable_pages[{document_name,page_no}]`, `closest_documents` | Yes |
| Not found: "I searched 46 documents" | none | **NB-24** |
| Devanagari keys | | Client |
| "satbara" → सातबारा / 7/12 suggestions | the transliteration library is backend only; My words is available | Partial: My words matches only |
| Cited spot side panel | the citation's page image + regions | Yes |

### 3.9 Search (`desktop/Search.html`, States "no results")

| Element | Source | Status |
|---|---|---|
| Inside documents | `GET /search?q&…filters` (keyword only, My words applied) | Yes |
| Names | `GET /documents?q=` | Yes |
| Filters: Department, Type, Label, Owner, Document date, Has newer version | `department_id, doc_types[], sensitivity[], owner_id, doc_date_from/to, has_newer_version` | Yes |
| "Also searched: 7/12 (from your words)" | `understood_words[{word,meaning}]` | Yes |
| "Also searched": glossary and Latin spellings (सातबारा, वाकड) | not returned | **NB-25** |
| Result: name, type, page(s), snippet with highlights | `documents[{id,display_name,doc_type,best{snippet,page_from,page_to,regions},more}]` | Yes |
| Result: version, label, folder path | not in search results | **NB-26** (small) |
| Result notes ("area needs review", "page 2 couldn't be read") | none | **NB-26** |
| "14 results in 9 documents" | documents + `more` | Client |
| Sort: date, last updated | best match only | **NB-27** |
| "Show 9 more" | capped at `SEARCH_MAX_DOCUMENTS`, no offset | **NB-27** |

### 3.10 Review (`desktop/Review.html`, `phone/PhoneReview.html`, `flows/FlowReview.html`)

| Element | Source | Status |
|---|---|---|
| Queue, total | `GET /review?limit&offset&document_id&reason[]` → `items[{value,document_name,page,check_reasons}], total` | Yes |
| Reason chips with counts | one `GET /review?reason=…&limit=1` per chip (4 calls) | Partial (works; a counts field would be better, NB-28) |
| Reasons: Two readers disagreed / Hard to read | `readers_disagree` / `unsure_text, low_confidence, ocr_script` | Yes |
| Reason: Handwritten | no value-level reason (page `read_issues` has `handwriting_hard`) | **Won't build**: chip dropped (D-9) |
| Spot crop | page image + `value.region` | Yes |
| Two readings | `value.value` / `value.second_reading` | Yes |
| "Matches the words: …" check note | none | **NB-29** |
| "used in 1 answer" | none | **NB-29** |
| Use first, Use second, Type the value, Remember | `POST /review/{value_id} {choice:first|second|typed,value?,remember}` | Yes |
| Skip, Group by document, keyboard shortcuts, "5 cleared this session" | | Client |
| Word by word | `GET /documents/{id}/flags`, `POST …/flags/{key}/confirm|edit`, `GET …/changes`, `POST …/changes/{id}/undo`, `GET …/review-status` | Yes |
| Empty "You cleared 23 this week" | none | **NB-30** |

### 3.11 My words (`desktop/MyWords.html`)

| Element | Source | Status |
|---|---|---|
| List: word, means, added date | `GET /me/words` → `items[{id,word,meaning,source,built_in,created_at,updated_at}]` | Yes |
| Count, last added | | Client |
| Add | `POST /me/words {word,meaning}`; 409 `word_has_meaning` → "Add another meaning?" with `confirm_another` / `replace_id` | Yes |
| Edit | `PATCH /me/words/{id}` | Yes |
| Delete (inline confirm) | `DELETE /me/words/{id}` | Yes |
| "Used in 18 documents · 6 questions", "Where 'बोजा' was used" | only per document (`GET /documents/{id}/my-words`) | **NB-31** |
| "Kaagaz found these words often" | none | **NB-32** |

### 3.12 Admin (`desktop/Admin.html`, `AdminTabs.html`)

**Users**

| Element | Source | Status |
|---|---|---|
| Table: name, email, role, two-step, status Active / Locked / Disabled | `GET /admin/users` → `display_name, email, role, totp_enabled, locked_until, disabled, must_change_password, created_at` | Yes |
| Status "Invited" | no invites (an admin creates a user with a temporary password) | **NB-33** |
| Departments column | no user-department link (access control isn't built yet) | **NB-34** |
| Last active | `users.last_seen_at` exists (0040) but isn't in `AdminUser` | **NB-35** (small) |
| Role select | `PATCH /admin/users/{id} {role}` | Yes |
| "Invite people" | `POST /admin/users {email,display_name,role,password}` | Yes, as "Add person"; the temporary password is shown once |
| Reset password | `POST …/reset-password` returns `temporary_password` | Yes: the temporary password is shown once, and the screen says so (not an email link) |
| Unlock, Turn off two-step (reset), Turn back on, Remove | `POST …/unlock`, `POST …/reset-2fa`, `PATCH {disabled:false}`, `PATCH {disabled:true}` | Yes |
| Drawer: lock reason with place and browser, uploaded count, last sign-in | none | **NB-36** |

**Departments, settings and activity**

| Element | Source | Status |
|---|---|---|
| Departments: list, order, add, rename | folders with `kind=department`: `POST/PATCH /folders {name,kind:"department",sort_order}` | Yes |
| Departments: file counts | `dashboard.departments[].files` | Yes |
| Departments: "who" (members) | | **NB-34** |
| Settings: Largest file, Auto-filing | `GET/PATCH /admin/settings {max_upload_mb, autofile_on}` | Yes |
| Settings: Upload limit per batch, Link versions on their own, Default label | none | **NB-37** |
| Settings: Sign out after | the idle timeout is operator-set | **NB-37** |
| Settings: Two-step for admins (locked On) | always on (0035) | Yes (display only) |
| Activity: filters Person, Folder, File, Action, Dates | `GET /admin/audit?user_id&folder_id&document_id&action[]&from&to&cursor` → `items, next_cursor, total` | Yes |
| Activity: Export to Excel | `GET /admin/audit/export` (CSV, opens in Excel) | Yes |
| Activity: device and place ("Chrome on Android · Pune") | none | **NB-36** |

**Numbers and failed files**

| Element | Source | Status |
|---|---|---|
| Numbers | `GET /admin/stats`, `GET /admin/usage?from&to` | Partial: values waiting, documents, questions asked and usage; Step 4 reads the exact fields |
| Plan quotas: "of 25,000 pages", "41.2 of 100 GB" | none | **NB-38** |
| Pages per week chart | `/admin/usage` by date range (5 calls), or a per-week series | Partial |
| Usage by department | none | **NB-39** |
| Failed files: list | `GET /admin/failed` → `display_name, failure_code, message, last_run_at` | Yes |
| Failed files: Retry, Retry all | `POST …/reprocess` | Yes |
| Failed files: uploaded by, department | none | **NB-40** (small) |
| "Password protected: Enter password" | no unlock flow | **NB-41** |

### 3.13 Operator console (`desktop/Operator.html`, `OperatorOrg.html`)

| Element | Source | Status |
|---|---|---|
| Organisations: name, id, status, users, documents, pages, storage, last activity | `GET /operator/orgs` | Yes |
| Organisations: plan, page quota %, review count, paused-unpaid | none | **NB-42** |
| Add organisation | `POST /operator/orgs` | Yes |
| Tiles: being read now, oldest waiting, failures today, queue time | `GET /operator/health` (`reader`, `queues`, `totals`), `GET /operator/insights` | Partial (exact fields checked in Step 4) |
| Live jobs list (job id, pages, wait) | none | **NB-43** |
| Failures by reason, 7 days | `GET /operator/insights|quality?days=7` | Partial |
| Org page: disable, enable, delete, admins | `POST /operator/orgs/{id}/…`, `…/admins` | Yes |
| Org page: feature switches | `GET/PUT /operator/orgs/{id}/feature-switches/{f}` (names `scan_ai, ask, ensemble, autofile, cost_saving, review, my_words, version_spotting`; the design's keys `filing.auto`… are examples) | Yes |
| Org page: session timeout | `GET/PUT /operator/orgs/{id}/session-timeout` | Yes |
| Models and costs | `GET /operator/models`, `GET /operator/usage`, `/model-board` | Yes |
| Org page: revenue | none | **NB-42** |
| Raise page limit, View as admin | none | **NB-44** |
| Operator activity | `GET /operator/activity?before&org&action` | Yes |

### 3.14 System states (`desktop/States.html`)

| State | Source | Status |
|---|---|---|
| Empty folder, Empty Ask, Search no results | list / answer / search replies | Yes |
| Empty Review "cleared this week" | | **NB-30** |
| Loading skeletons | | Client |
| Error with code and time | `ApiError`, `X-Request-ID` header for the code | Yes |
| Session expired dialog | 401 `not_signed_in` → existing ended flow (`lib/session.tsx`) | Yes |
| Session expired: re-enter password inline | | Partial (sends to sign-in, keeps the page to come back to) |
| Toast for a failed file | batch polling | Yes |
| 404 | `app/not-found.tsx` | Yes |
| Offline: open documents viewed today, queue uploads | no offline cache or service worker | **NB-45** (frontend-heavy; propose to skip) |
| No permission, Request access | no access control yet | **NB-46** |

---

## 4. Needs backend: the full list

Each one leaves its UI out (or uses the noted fallback) behind `TODO(needs-backend: NB-xx)` until it lands. Size: S = a field or filter on an existing query; M = a new endpoint; L = a new feature.

| # | What the UI needs | Screens | Size | Priority |
|---|---|---|---|---|
| NB-01 | Theme saved on the user's profile (today: cookie per browser) | Shell | S |  |
| NB-02 | Notifications (bell count, list, "ready" notices) | Shell, Upload | L |  |
| NB-03 | Backup codes for two-step | Sign in | M |  |
| NB-04 | Forgot / reset my password by email | Sign in | M |  |
| NB-05 | "Keep me signed in" | Sign in | S |  |
| NB-06 | Review count per department | Home | S |  |
| NB-07 | New versions split: linked by people vs on their own | Home | S |  |
| NB-08 | Last updated per department | Home | S |  |
| NB-09 | Folder path on dashboard recent documents | Home | S | 1 |
| NB-10 | Recently viewed by me | Phone Home | M |  |
| NB-11 | Max files per batch in `/upload-limits` | Upload | S | 1 |
| NB-12 | Why a file was filed or re-filed | Upload | S |  |
| NB-13 | File counts per folder in `GET /folders` | Documents | S | 1 |
| NB-14 | Marathi / Hindi labels for document types, folder subtitles and value fields | Documents, Document page | S |  |
| NB-15 | `doc_types[]` filter on `GET /documents` | Documents | S | 1 |
| NB-16 | Label on related documents | Document page | S | 1 |
| NB-17 | Link reason (overlap %, shared id) on each version | Document page | S |  |
| NB-18 | Plain-words explanation of each change in Compare | Compare | M |  |
| NB-19 | Version number on each citation | Ask | S | 1 |
| NB-20 | "Scope widened" flag on answers | Ask | S |  |
| NB-21 | Table answers with the rows behind a total, and their export | Ask | M | 2 |
| NB-22 | Both readings on a needs-review citation | Ask | S |  |
| NB-23 | Streamed live steps and answers that survive a reload | Ask | L | 2 |
| NB-24 | How many documents were searched, on Not found | Ask | S |  |
| NB-25 | Glossary / Latin-spelling expansions in `understood_words` | Search | S |  |
| NB-26 | Version, label, folder path and page notes on search results | Search | S | 1 |
| NB-27 | Search sorting and "show more" paging | Search | M |  |
| NB-28 | Review counts by reason | Review | S |  |
| NB-29 | Review check notes ("matches the words") and "used in N answers" | Review | M |  |
| NB-30 | Values cleared this week | Review | S |  |
| NB-31 | Where each of My words was used | My words | M |  |
| NB-32 | Suggested words | My words | M |  |
| NB-33 | Invites | Admin | M |  |
| NB-34 | People in departments (comes with access control) | Admin | L |  |
| NB-35 | Last active on admin users | Admin | S | 1 |
| NB-36 | Sign-in device and place, lock events, uploads per user | Admin | M |  |
| NB-37 | Org settings: batch limit, link on their own, default label, sign out after | Admin | M |  |
| NB-38 | Plan quotas (pages, storage) | Admin, Operator | M |  |
| NB-39 | Usage by department | Admin | S |  |
| NB-40 | Uploader and department on failed files | Admin | S |  |
| NB-41 | Unlock a password-protected file | Admin, Upload | M |  |
| NB-42 | Plans, page quotas and revenue per org | Operator | M |  |
| NB-43 | Live job list | Operator | M |  |
| NB-44 | Raise page limit, view as admin with approval | Operator | M |  |
| NB-45 | Offline mode | States | L |  |
| NB-46 | Access control and Request access | States | L |  |

Priority (approved 20 Oct 2026): first NB-09, 11, 13, 15, 16, 19, 26 and 35, small query additions that remove most fallbacks on the main screens; then NB-23 and NB-21. This is a list only: the UI work builds none of them. Every gap stays behind `TODO(needs-backend: NB-xx)` and out of the UI, and no screen shows made-up data.

---

## 5. Decisions (approved 20 Oct 2026)

| # | Decision |
|---|---|
| D-1 | The handoff is committed as `design/kaagaz-ui-handoff/` (the updated zip). |
| D-2 | Theme: Light, Dark and System, in the `kaagaz_theme` cookie. The dark block is also emitted under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { … } }`. |
| D-3 | Icon stroke 1.6 everywhere. |
| D-4 | Review keys: 1 = first reading, 2 = second, T = type the value, Enter = save and next, J/K = next and previous. |
| D-5 | No "Keep both" for duplicates; "Skip this file" and "Open it". |
| D-6 | No locked-account screen and no tries left; one message. |
| D-7 | Review is reviewer and admin only; members see no Review nav item or count. |
| D-8 | The Questions tab shows only the signed-in person's own questions. |
| D-9 | No "Handwritten" chip. |
| D-10 | `@playwright/test` as a dev dependency, for screenshot checks only. |
| D-11 | This file stays during the UI work and is folded into `docs/architecture.md` at the end. |
| D-12 | Upload is a drop zone plus a batch tray visible on every page, not a separate `/upload` page. The top-bar Upload button opens the file picker and the tray. |

## 6. Steps

All four steps go in one pull request into `feature/UI-UX`.

1. **Foundation**:
   - `app/styles/tokens.css` (light, dark, and the System media block) and `base.css`
   - fonts swapped
   - theme on the new tokens
   - `Icon` with the handoff paths
   - `lib/status.ts`
   - the guard test allows `tokens.css`
2. **UI kit**: the components in `components/ui/<Name>/`, each with a render test against its states.
3. **AppShell**: sidebar, top bar, account menu, phone tab bar, new `/home` and `/search` routes.
4. **Screens**, in this order: Home → Documents → Upload tray → Document page → Version + Compare → Ask → Search → Review → My words → Admin → Sign in → Operator → States.
   - Each screen: wired to real data, the fallbacks above, and typecheck, lint and tests green.
   - Backend unchanged throughout.
