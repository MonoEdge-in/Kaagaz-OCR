// The API contract, as the backend sends it (backend/kaagaz/api). Every field
// named here exists in a response; nothing is optional unless marked so.

export type Role = "member" | "reviewer" | "admin";

export type DocumentStatus =
  | "received"
  | "clean"
  | "rejected"
  | "needs_reader"
  | "ready"
  | "failed";

export type DetectedType = "pdf" | "png" | "jpeg" | "tiff" | "zip" | "ole2" | "text";

export type Label = "verified" | "needs_review";

export type LabelReason =
  | "direct_cell"
  | "readers_agree"
  | "readers_disagree"
  | "single_reader"
  | "low_confidence"
  | "corrected"
  | "learned_correction"
  | "model_chosen" // PRD 2: the AI model chose its field (card and invoice values)
  | "ocr_script" // PRD 2: OCR read it from Devanagari or mixed-script text
  | "check_failed" // PRD 2, K3: a check on its invoice failed, even if the readers agreed
  | "unsure_text" // PRD 5 V4: it holds a word the reader was unsure of, or [?]
  | "box_unanchored" // PRD 5 G5: its spot on the page couldn't be confirmed
  | "local_reader"; // PRD 5 Q7: read by the backup reader while the main reader was down

/** "admin" only for an admin's session (from /login, always with two-step since 16 Oct 2026). */
export type SessionScope = "app" | "admin";

// must_change_password and session are always sent (backend api/views.py
// me_json). Read them through components/shell/gate.ts, never directly, so
// each rule lives in one place.
export interface Me {
  user: {
    id: string;
    email: string;
    display_name: string;
    role: Role;
    must_change_password: boolean;
    totp_enabled: boolean; // two-step sign-in is on (required for an admin)
    can_delete_forever: boolean; // an admin allowed this member or reviewer (never an admin)
  };
  customer: { id: string; name: string };
  session: { scope: SessionScope };
}

/** "Turn on" two-step sign-in (POST /auth/admin/2fa/start). Nothing is stored yet. */
export interface TwoStepSetup {
  enrol_token: string; // lasts 10 minutes
  secret: string; // base32, in groups of 4
  otpauth_uri: string; // the same key as an otpauth:// link, for a QR code
}

// Where a value sits. A box is in fractions (0 to 1) of the page's width and
// height, origin top left. A cell is 1-based; for spreadsheets, page = sheet.
export type Region =
  | { kind: "box"; page: number; box: [number, number, number, number] }
  | { kind: "cell"; page: number; row: number; column: number };

export interface DocumentSummary {
  id: string;
  display_name: string;
  detected_type: DetectedType;
  size_bytes: number;
  status: DocumentStatus;
  failure_code: string | null;
  status_message: string; // safe to show as is
  page_count: number | null;
  created_at: string; // ISO 8601
  uploaded_by_name: string | null;
  folder_id: string | null; // null: at the top level, in no folder
  tags: Tag[];
  deleted_at: string | null; // set while the document (its whole family) is in Trash
  version_no: number; // 1 for the first upload
  family_id: string; // shared by every version of one document
  can_delete: boolean; // may move the family to Trash and restore it: an admin, or who uploaded every version
  attempts: number; // reads tried so far; status_message already says "Try 2 of 5"
  max_attempts: number | null;
  newer_version: NewerVersion | null; // a later version still being read, or one that failed
  progress: DocumentProgress | null; // null unless it is being checked or read; status_message already says it
  time_left: TimeLeft | null; // null until preparing (PRD 4 #13): while scanning, waiting or done; never in Trash
  understanding?: boolean; // Ready first (16 Oct 2026): ready and usable, its card and map still coming
  doc_type: DocType | null; // from the AI model's document card; null without one (PRD 2, D6)
  languages: string[]; // "en", "hi", "mr", "other"; [] when not known
  // PRD 5 F4: pages unreadable or uncertain still on the attention list (a page kept as is
  // has left it), and the values labelled needs review.
  pages_unreadable: number;
  pages_uncertain: number;
  values_to_review: number;
  words_unsure?: number; // PRD 5 U2: the unsure words on its pages, for the "14 words unsure" chip
  // PRD 5 U1: from GET /api/batches/{id} only: its first unreadable page still to look at, or null.
  first_unreadable?: FirstUnreadablePage | null;
  values_found?: number; // PRD 5 U1: from GET /api/batches/{id} only: the values extracted from it
  // Feature 28: Kaagaz filed it into its folder by type ("filed", Undo offered), or could not
  // tell where it belongs ("not_filed"); null when auto-filing had nothing to do with it.
  autofile?: "filed" | "not_filed" | null;
  // File management (PRD 1, Phase 2: O1 to O3, F5, Q3): the family's owner and label, this
  // version's note, the card's date, the last change and who made it, its department, and a
  // version suggestion waiting for a person (V5). Optional: older replies lack them. (The
  // list's count of versions is left out: the detail's `versions` is the list itself.)
  owner?: { id: string; name: string | null } | null;
  sensitivity?: Sensitivity;
  version_note?: string | null;
  document_date?: string | null; // YYYY-MM-DD
  updated_at?: string | null;
  updated_by?: string | null;
  department?: { id: string; name: string } | null;
  // "new": this document may be a new version of another; "candidate": a newer one waits on this family.
  pending_suggestion?: { id: string; role: "new" | "candidate" } | null;
}

/** The four sensitivity labels (O2). They inform; they restrict nothing. */
export type Sensitivity = "public" | "internal" | "confidential" | "restricted";

/** What a folder is (F1): a department is a top-level folder an admin made. */
export type FolderKind = "department" | "category" | "party" | "property" | "year" | "custom";

/**
 * A suggestion that an upload is a new version of another family (V5, V6): GET
 * /documents/{id}'s `version_suggestion`, and GET /batches/{id}'s `version_suggestions`. The
 * scores are 0 to 1, or null when that signal did not count; `reasons` are the server's plain words.
 */
export interface VersionSuggestion {
  id: string;
  role: "new" | "candidate"; // this document is the new upload, or its family is the candidate
  outcome: "suggested" | "auto";
  status: "pending" | "accepted" | "rejected" | "undone";
  document: { id: string; name: string }; // the new upload
  candidate: { family_id: string; id: string; name: string }; // id: the family's current version
  next_version_no: number; // "Make it version N"
  text_overlap: number | null;
  name_similarity: number | null;
  shared_identifier: string | null; // its kind, such as "survey_number"
  reasons?: string | string[] | null;
}

/** GET /api/dashboard (D1, D2). */
export interface Dashboard {
  tiles: {
    documents: number;
    documents_this_week: number;
    departments: number;
    new_versions_this_month: number;
    versions_to_confirm: number;
  };
  by_type: { doc_type: DocType | null; count: number }[];
  departments: { id: string; name: string; sort_order: number | null; files: number }[];
  not_in_department: number;
  recent: {
    id: string;
    display_name: string;
    doc_type: DocType | null;
    sensitivity: Sensitivity;
    version_no: number;
    status: DocumentStatus;
    created_at: string;
  }[];
  activity: ActivityEntry[];
}

/** One row of a timeline (A5) or of the admin log: an AuditEntry with the names filled in. */
export type ActivityEntry = AuditEntry;

/** A page of a timeline or of the admin log, newest first; `next_cursor` asks for more (null: no more). */
export interface ActivityPage {
  items: ActivityEntry[];
  next_cursor?: string | null;
  total?: number; // the admin log only
}

/** A question this person asked about the document (P2), newest first. */
export interface DocumentQuestion {
  id: string;
  conversation_id: string;
  question: string;
  asked_at: string;
  answer: Answer | null;
}

/** GET /api/documents/{id}/compare?with= (V11): from `with` (left) to `id` (right), page by page. */
export type CompareKind = "same" | "added" | "removed";
export interface CompareRun {
  kind: CompareKind;
  text: string;
}
export interface DocumentCompare {
  left_id: string;
  right_id: string;
  summary: string; // "2 changes on page 2"
  pages: { page_no: number; changes: number; truncated: boolean; runs: CompareRun[] }[];
}

/** An active person of this company, for the owner picker and the filters. */
export interface Person {
  id: string;
  name: string;
}

/** A file's first unreadable page, for the tray's "Couldn't read page n" (U1): what
 *  happened and the fix, from the page's first problem; null when none can be said. */
export interface FirstUnreadablePage {
  page_no: number;
  says: string | null;
  fix: string | null;
}

/** GET /api/batches/{id} (PRD 5 Q1): one drop of files, the uploader's own only, at most
 *  MAX_FILES_PER_BATCH. A file refused as a duplicate is not in it: the tray keeps those. */
export interface BatchView {
  id: string;
  documents: DocumentSummary[]; // in upload order; the tray sorts them worst first
  counts: { total: number; ready: number; reading: number; waiting: number; attention: number; failed: number };
  pages_read: number; // of pages_total (U1); the bar weighs each document's own fraction (batchTray.ts)
  pages_total: number;
  time_left: TimeLeft | null; // until the whole drop is ready; null when nothing is left to read
  // V5 (stage 2): the files of this drop that look like new versions, each waiting for a person.
  version_suggestion_count?: number;
  version_suggestions?: VersionSuggestion[];
}

/** GET /api/search (PRD 3 Q2): the documents found, best first, at most SEARCH_MAX_DOCUMENTS. */
export interface DocumentSearch {
  documents: FoundDocument[];
  story_matches?: unknown; // checked by lib/storyMatches.ts before use
  understood_words?: { word: string; meaning: string; origin?: "personal" | "organization" }[];
}

/** How well a question tells a story the user wrote again (S3): never a score. */
export type StoryBand = "match" | "maybe";

/** One of the user's own documents whose story the question tells again; never the story. */
export interface StoryMatch {
  document_id: string;
  display_name: string;
  doc_type: DocType | null;
  band: StoryBand;
}

export interface FoundDocument {
  id: string;
  display_name: string;
  doc_type: DocType | null;
  score: number;
  best: { snippet: string; page_from: number; page_to: number; regions: Region[] }; // its best piece
  more: number; // its other pieces among the candidates
  // Q5 (stage 3): its version, label and folder path.
  version_no?: number;
  versions?: number;
  sensitivity?: Sensitivity;
  folder_path?: string | null;
}

/** What kind of document the AI model's card says it is (PRD 2, D1). */
export type DocType =
  | "invoice"
  | "receipt"
  | "bank_statement"
  | "land_record"
  | "contract"
  | "register"
  | "form"
  | "letter"
  | "other";

/** How far the worker has got (PRD 2, X3), e.g. step "reading", done 3, total 40. */
export interface DocumentProgress {
  // "waiting" (PRD 5 Q6): held back in its batch or for the reader; the status message says why.
  // "rereading": some pages read again carefully (Dynamic's Careful); total is how many.
  step: "scanning" | "preparing" | "reading" | "rereading" | "understanding" | "indexing" | "waiting";
  done: number;
  total: number; // 0 for a sheet, which is read as a whole
}

/** How long until ready (PRD 4 #13), at the moment the server answered. lib/timeLeft.ts counts it down. */
export interface TimeLeft {
  seconds: number; // until ready, never below 0
  since_move_seconds: number; // since a page was last drawn or read, or reading ended
  slow_after_seconds: number; // past this with no move, it is taking longer than usual
  items_left: number; // pages still to draw (preparing) or read; 0 once reading has ended
  item_seconds: number; // expected seconds a page
  after_seconds: number; // expected seconds from the end of this step's pages until ready
  confidence: "low" | "high"; // low: not enough of this customer's history; shown as "About ..." like any estimate
  elapsed_seconds: number; // since this run started: the percentage is elapsed / (elapsed + left)
  done_fraction: number; // 0 to 1, the server's, the same on every load: a refreshed bar starts from it
}

/** A version newer than the one listed (the family's current), not in use yet. */
export interface NewerVersion {
  id: string;
  version_no: number;
  status: DocumentStatus;
  failure_code: string | null;
  status_message: string; // safe to show as is
}

/** One version of a document. Each is its own document id; the viewer opens any of them. */
export interface DocumentVersion {
  id: string;
  version_no: number;
  status: DocumentStatus;
  is_current: boolean; // the one search and chat use: the highest version that is ready
  created_at: string;
  // V12 (stage 2): its note, who uploaded it, and how it joined the family.
  version_note?: string | null;
  display_name?: string;
  uploaded_by?: { id: string; name: string | null } | null;
  joined?: "uploaded" | "linked" | "auto";
}

// A folder of this customer. The API sends the flat list; the screen builds the
// tree (components/documents/folderTree.ts). Folder and tags belong to the
// document's family: every version shares them.
export interface Folder {
  id: string;
  name: string;
  parent_id: string | null; // null: at the top level
  created_at: string;
  kind?: FolderKind; // F1; left out by older replies: custom
  sort_order?: number | null; // a department's place on the dashboard and in the tree
}

export interface Tag {
  id: string;
  name: string;
}

/** Where a document is filed: its folder, and the folders from the top down (`path`). */
export interface DocumentFolder {
  id: string;
  name: string;
  path: { id: string; name: string }[];
}

/** How a page was read (PRD 2, R1). */
export type PageRoute = "cells" | "text_layer" | "ocr" | "blank";

/** How well a page was read (PRD 5 F1, F2). Text-layer and cell pages are good. */
export type ReadQuality = "good" | "uncertain" | "unreadable";

/** Why a page is not good (PRD 5 F1's issue table). */
export type ReadIssue =
  | "blurry"
  | "low_resolution"
  | "faint"
  | "glare"
  | "shadow"
  | "cut_off"
  | "stamp_over_text"
  | "overwritten"
  | "handwriting_hard"
  | "illegible_words"
  | "readers_disagree"
  | "unpinned"
  | "reader_refused"
  | "read_locally"
  | "no_text";

export interface PageSummary {
  page_no: number;
  width: number;
  height: number;
  unit: "pt" | "px" | "cells";
  has_image: boolean;
  route: PageRoute | null; // null: not read (yet)
  effective_dpi: number | null; // of the largest image on the page; null when it has none
  low_resolution: boolean; // effective_dpi under LOW_RES_DPI; PRD 3 shows the warning
  // PRD 5 RT2: the reading engine's ID (the model's, "rapidocr", or an old reader's the
  // viewer shows as "Read by OCR"), "text_layer" or "cells"; null for a blank page or one
  // read before PRD 5. A page the
  // main reader handed to the backup reader has read_locally or reader_refused in read_issues.
  read_by: string | null;
  read_by_name?: string | null; // model choice: the model's name ("Gemini 3.1 Pro") when a model read it
  read_quality: ReadQuality; // F1
  read_issues: ReadIssue[]; // F1; [] when good
  unsure_words: number; // words the reader listed as unsure
  illegible_words: number; // parts marked [?], which couldn't be read
  unpinned_lines: number; // lines whose spot no found box confirmed (G5)
  accepted_by: string | null; // F5 "Keep as is": the user, with accepted_at; the page stays flagged
  accepted_at: string | null; // ISO 8601
  read_again_requested_at: string | null; // F5: asked to be read again, until it is
  problems?: PageProblem[]; // PRD 5 U4: one per issue in read_issues, for the page's banner
}

/** A problem on a page (PRD 5 U4), in the words of F1's issue table. */
export interface PageProblem {
  code: ReadIssue;
  says: string; // what happened, in one line
  fix: string; // what the user can do
}

/** A reading problem drawn on the page (PRD 5 U3): dashed, hatched or dotted, with a label. */
export interface ReadingMark {
  quality: "unsure" | "illegible" | "unpinned";
  region: Region;
  text: string; // the word or line as read: plain text only, through visibleText
}

export interface ExtractedValue {
  id: string;
  document_id: string;
  record_key: string;
  field: string;
  value: string;
  raw: string;
  second_reading: string | null;
  page_no: number;
  region: Region;
  label: Label;
  label_reason: LabelReason;
  confidence: number | null;
  label_page_no: number | null; // the value's label spot, which can be on another page (PRD 2 D3); null without one
  label_region: Region | null;
  votes?: ValueVotes | null; // Ensemble (feature 27): the readers' vote, saved with the value; null: one reader
}

/** Ensemble's vote on one value (feature 27, Tushar 15 Oct 2026): what each reader read at the
 * value's spot, the main reader first (text null: it read nothing there), how many read the
 * decided value (value null: no majority, so the value needs review). Never a model's name. */
export interface ValueVotes {
  value: string | null;
  agreed: number;
  readers: number;
  reads: { main: boolean; text: string | null }[];
}

/** The AI model's document card (PRD 2 D1, D3): what the document is, and where each
 *  party, identifier, date and key fact sits. The title and summary are the model's own
 *  words (PRD 3 labels them "AI summary, check the pages"); every other text is the
 *  reader's text at its spot. */
export interface DocumentCard {
  version: number;
  run_id: string | null;
  model: string;
  prompt: string; // which prompt and schema made the card (a hash)
  doc_type: DocType;
  languages: string[];
  title: string | null;
  summary: string | null;
  document_date: { value: string; date: string | null; page: number; region: Region } | null;
  parties: {
    role: string;
    field: string;
    name: string;
    name_latin: string | null; // a Latin spelling of a Devanagari name (PRD 2 D4)
    page: number;
    region: Region;
  }[];
  identifiers: { kind: string; field: string; value: string; page: number; region: Region }[];
  key_facts: {
    field: string;
    value: string;
    page: number;
    region: Region;
    label: string | null;
    label_page: number | null;
    label_region: Region | null;
  }[];
  dropped_quotes: number;
}

/** One line item of an invoice (PRD 2 E5). Numbers are strings, as the API sends them,
 *  so no float rounding happens. */
export interface InvoiceLine {
  line_no: number;
  description: string | null;
  hsn: string | null;
  quantity: string | null;
  unit: string | null;
  rate: string | null;
  amount: string | null;
  label: Label;
}

/** A document's invoice as a typed row (PRD 2 E5, K2), with its check. Every value in it
 *  is also an ExtractedValue with its spot, so it can be shown on its page. */
export interface Invoice {
  invoice_number: string | null;
  invoice_date: string | null; // YYYY-MM-DD
  seller_name: string | null;
  seller_gstin: string | null;
  buyer_name: string | null;
  buyer_gstin: string | null;
  subtotal: string | null;
  tax_total: string | null;
  total: string | null;
  currency: string;
  label: Label;
  check_status: "passed" | "failed" | "not_checked";
  check_reasons: string[];
  lines: InvoiceLine[];
}

export interface DocumentDetail extends DocumentSummary {
  pages: PageSummary[];
  values: ExtractedValue[];
  folder: DocumentFolder | null; // null: at the top level
  versions: DocumentVersion[]; // every version of the family, this one included
  reviewer_fixes: number; // corrections on this document's values; said before "Read again" (PRD 2, X1)
  summary: string | null; // the AI model's own words, plain text (PRD 3 labels it); null without a card
  document_date: string | null; // YYYY-MM-DD, from the card; null without one
  card: DocumentCard | null; // null without a card (the model off or down)
  invoice: Invoice | null; // null for a document with no invoices row
  links: DocumentLink[]; // current, ready documents sharing an identifier (PRD 3 Y2); at most 50
  read_model: string | null; // the model it asked to be read with (MS5); null: the organisation's default
  // P1, V6 (stage 2): who uploaded version 1, and a version suggestion about this document.
  author_name?: string | null;
  version_suggestion?: VersionSuggestion | null;
}

/** A related document (PRD 3 Y2). The value is stored normalised: no spaces, lower case. */
export interface DocumentLink {
  id: string;
  display_name: string;
  doc_type: DocType | null;
  identifier: { kind: string; value: string };
}

export interface DocumentList {
  items: DocumentSummary[];
  total: number;
}

/** GET /upload-limits: the largest file of each kind, already lowered by the customer's own limit. */
export interface UploadLimits {
  pdf_bytes: number;
  image_bytes: number;
  sheet_bytes: number;
  max_pages: number; // for a PDF's pages and a TIFF's frames
  readers: UploadReader[]; // the scan readers an upload may choose, the server's default first
}

/** What an upload or a whole document's Read again asks for (feature 26); nothing: the default. */
export interface UploadReading {
  read_mode?: "dynamic";
  read_model?: string;
}

/** One scan reader the server offers (PRD 4 #17): reason says why it can't be used here. */
export interface UploadReader {
  id: string;
  title: string;
  hint: string;
  available: boolean;
  reason: string | null;
  default?: boolean; // a model: the organisation's default scan model (MS4, R14); Auto has none
}

export interface UploadResult {
  document: DocumentSummary;
  duplicate: boolean;
  in_trash: boolean; // a duplicate of a document that is in Trash
}

export interface SheetCells {
  page_no: number;
  total_rows: number;
  rows: { row: number; cells: { column: number; text: string }[] }[];
}

/** GET /documents/{id}/pages/{n}/readings: what each reader read on one page, as read. */
export interface PageReadings {
  page_no: number;
  readers: PageReader[]; // the first reader first; [] when the page was not read yet
  marks?: ReadingMark[]; // PRD 5 U3: the page's reading problems; [] when it has none
}

export interface PageReader {
  role: "first" | "second";
  reader: string;
  reader_version: string;
  truncated: boolean; // more lines were read than are sent (2,000 lines or 100,000 characters)
  lines: ReadLine[]; // in the order they were read
}

export interface ReadLine {
  text: string; // exactly as read (the raw): show as plain text only, through visibleText
  confidence: number | null; // null for a direct read, or a reader that gives none
  origin: "cell" | "text_layer" | "ocr" | null;
  kind: string | null; // a layout block kind, when the reader gives one
  region: Region;
  table?: { id: string; row: number; col: number; header: boolean } | null; // a table cell's place
}

/** GET /documents/{id}/clear-copy (Read clearly): every page's main reader lines, in order. */
export interface ClearCopy {
  pages: { page_no: number; lines: ReadLine[]; marks: ReadingMark[] }[];
  truncated: boolean; // the document-wide cap was reached: the rest was left out
}

export type TranslateLanguage = "en" | "hi" | "mr";

/** POST /documents/{id}/pages/{n}/translate: one translation for each line, in order. */
export interface PageTranslation {
  page_no: number;
  language: TranslateLanguage;
  lines: string[]; // plain text only, through visibleText
  truncated: boolean; // only the first lines of a long page were translated
  cached: boolean;
}

/** What became of a cited document since the answer was given. Missing means "live". */
export type SourceState = "live" | "older_version" | "in_trash" | "deleted";

export interface Citation {
  n: number; // what sentences refer to, from 1
  kind: "piece" | "value";
  ref_id: string;
  document_id: string;
  document_name: string;
  detected_type: DetectedType;
  page_no: number;
  regions: Region[]; // [] once the document is in Trash or deleted
  snippet: string | null; // plain text, never markup; null once in Trash or deleted
  label: Label;
  source_state?: SourceState;
  // PRD 3 H4. Missing on answers saved before PRD 3, so they are read with a fallback.
  page_from?: number; // the pages the source covers ("Pages 15 to 16")
  page_to?: number;
  label_page_no?: number | null; // where a value's label is, when it was found
  label_regions?: Region[]; // [] for a piece, and once the document is in Trash or deleted
  hard_to_read?: boolean; // PRD 5 F6: the cited text holds an unsure or illegible word; missing before it
  // Ranked sources (Tushar, 16 Oct 2026): the place in the search's order, 1 the most
  // relevant; null (or missing, before it) where there is no such order.
  rank?: number | null;
}

/** "Also in" (Tushar, 16 Oct 2026): a document the search found that the answer does not cite. */
export interface RelatedDocument {
  document_id: string;
  document_name: string;
  detected_type: DetectedType;
  page_no: number; // its best hit's page
  regions: Region[]; // that hit's spot on the page
  snippet: string; // plain text, never markup
}

/** "Also mentions" (Tushar, 17 Oct 2026): another document holding the question's key terms. */
export interface MentionedDocument {
  document_id: string;
  document_name: string;
  page_no: number; // its first matching piece's page
  pages: number[]; // every page a term is on, in order
  snippet: string; // two lines around the first match, plain text, never markup
}

export interface Mentions {
  terms: string[]; // the question's key terms, as typed
  total: number; // every such document; more than the ones listed when there are many
  documents: MentionedDocument[];
}

/** The source a person put on top of an answer ("Most relevant"). */
export interface SourcePin {
  document_id: string;
  page_no: number;
  ref_id?: string | null; // the cited source's, so one of two on the same page; none for Also in
}

/** A page a not-found answer names as not read well (PRD 5 F6), for its link. */
export interface UnreadablePage {
  document_id: string;
  document_name: string;
  page_no: number;
  read_quality: "uncertain" | "unreadable";
}

export interface AnswerSentence {
  text: string;
  citations: number[];
}

export interface Answer {
  // "unchecked": the figure asked for is only in unchecked card values, so no
  // number is given; closest_documents says where.
  status: "answered" | "not_found" | "unchecked";
  label: Label | null; // null when not found
  sentences: AnswerSentence[];
  citations: Citation[];
  closest_documents: { id: string; display_name: string }[];
  route: "fixed_query" | "search";
  normalized_question: string;
  model_used: boolean;
  rewritten_question?: string | null; // PRD 3 H1: what a follow-up was searched as; missing before PRD 3
  understood_words?: { word: string; meaning: string; origin?: "personal" | "organization" }[];
  // PRD 5 F6, missing before it: "Not found in the readable text. 2 pages couldn't be read: ..."
  // on a not-found answer, or "Part of this text was hard to read." on an answer that needs
  // review; null otherwise. The pages it names, and how many there are in all.
  reading_note?: string | null;
  unreadable_pages?: UnreadablePage[];
  unreadable_page_count?: number;
  related_documents?: RelatedDocument[]; // "Also in"; missing before 16 Oct 2026
  mentions?: Mentions; // "Also mentions"; only when there is one (17 Oct 2026)
  // Features 25-27 (C7), only on an answer drafted with the asker's answer settings: the
  // names (and ids) of the models that replied, the one used first; how many were asked;
  // whether the others confirmed it (null with one model); the thinking level.
  agents?: string[];
  agent_ids?: string[];
  agents_asked?: number;
  agreement?: "agreed" | "not_confirmed" | null;
  thinking?: ThinkingLevel;
}

export type ThinkingLevel = "low" | "medium" | "high";

/** GET /chat/options (features 25-27): the answer settings this organisation allows now. */
export interface ChatOptions {
  agents: { id: string; name: string; default: boolean }[]; // by name; [] when none is set
  levels: ThinkingLevel[];
  default_level: ThinkingLevel;
  ensemble: boolean; // more than one model may answer at once
  max_agents: number;
}

/** POST /chat's answer settings; a field left out is the organisation's default. */
export interface AnswerChoice {
  agents?: string[];
  thinking?: ThinkingLevel;
}

/** One of my words (GET /me/words). Text from the user: shown as text only. */
export interface MyWord {
  id: string;
  word: string;
  meaning: string;
  source: "manual" | "upload" | "viewer";
  built_in: boolean; // chat's own word list reads it too (the badge)
  created_at: string;
  updated_at: string;
}

/** POST /me/words: 201 created, 200 already there or replaced. */
export interface WordSaved {
  word: MyWord;
  outcome: "created" | "already" | "replaced";
  warning: "built_in" | null;
}

/**
 * Which documents a question may use (PRD 3 Q1, H2). A field left out filters
 * nothing, so {} is every document. The server checks every id again.
 */
export interface ScopeBody {
  folder_id?: string | null;
  include_subfolders?: boolean;
  tag_ids?: string[] | null;
  doc_types?: DocType[] | null;
  document_ids?: string[] | null;
  date_from?: string | null; // YYYY-MM-DD
  date_to?: string | null;
}

/** One of my words (GET /me/words). Text from the user: shown as text only. */
export interface MyWord {
  id: string;
  word: string;
  meaning: string;
  source: "manual" | "upload" | "viewer";
  built_in: boolean; // chat's own word list reads it too (the badge)
  created_at: string;
  updated_at: string;
}

/** One of my words on a spot of a page (GET /documents/{id}/my-words?page=): only mine. */
export interface MySpot {
  id: string;
  word_id: string;
  word: string;
  meaning: string;
  page_no: number;
  region: Region;
}

export interface OrganizationWord {
  id: string;
  word: string;
  meaning: string;
  status: "pending" | "approved" | "rejected";
  submitted_by: string;
  created_at: string;
  document_id: string | null;
  page_no: number | null;
}

/** POST /me/words: 201 created, 200 already there or replaced. */
export interface WordSaved {
  word: MyWord;
  outcome: "created" | "already" | "replaced";
  warning: "built_in" | null;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  created_at: string;
  answer: Answer | null; // set for assistant messages only
  scope?: ScopeBody | null; // the scope a question used (PRD 3 H2); null when it had none
  feedback: "up" | "down" | null; // Useful or Not useful (PRD 6 F1)
  pin?: SourcePin | null; // the source this person put on top of the answer; missing on a new reply
}

/** What went wrong on Not useful (PRD 6 F1; backend db/chat.py REASONS). */
export type ReasonCode = "wrong_documents" | "document_missing" | "wrong_figure" | "did_not_understand" | "other";

export interface ChatResponse {
  conversation_id: string;
  question: Message;
  answer: Message;
}

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
}

export interface ReviewItem {
  value: ExtractedValue;
  document_id: string;
  document_name: string;
  detected_type: DetectedType;
  page: PageSummary;
  check_reasons: string[]; // why its document's invoice check failed (PRD 3 Y5); [] otherwise
}

/** The review queue's reason filter (PRD 3 Y5; PRD 5 U6 adds the last four). */
export type ReviewReason =
  | "check_failed"
  | "readers_disagree"
  | "low_confidence"
  | "single_reader"
  | "unsure_text"
  | "box_unanchored"
  | "local_reader"
  | "ocr_script";

/** A row of the admin's Failed jobs tab (PRD 3 Z1). */
export interface FailedDocument {
  id: string;
  display_name: string;
  status: "failed" | "needs_reader";
  failure_code: string | null;
  message: string; // safe to show as is
  last_run_at: string | null; // null: never run
}

/** A row of the admin's Failed jobs tab (PRD 3 Z1). */
export interface FailedDocument {
  id: string;
  display_name: string;
  status: "failed" | "needs_reader";
  failure_code: string | null;
  message: string; // safe to show as is
  last_run_at: string | null; // null: never run
}

export interface ReviewList {
  items: ReviewItem[];
  total: number;
}

export type ReviewChoice = "first" | "second" | "typed";

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  disabled: boolean;
  created_at: string;
  locked_until: string | null; // ISO 8601; null when not locked (a passed lock is sent as null)
  totp_enabled: boolean;
  must_change_password: boolean;
  can_delete_forever: boolean; // an admin allowed Delete forever (never set for an admin)
}

/** A reset password: the temporary password is in this one reply only. */
export interface PasswordReset {
  user: AdminUser;
  temporary_password: string;
}

export interface CustomerSettings {
  customer_name: string;
  max_upload_mb: number | null; // null: the platform limit applies
  platform_max_upload_mb: number;
  // Feature 28: the platform allowed auto-filing (granted), and the admin switched it on.
  autofile: { granted: boolean; on: boolean };
}

export interface AuditEntry {
  id: number;
  action: string;
  // File management A3, A5: the row's family, its document (deleted: true once gone) and folder,
  // names filled in when shown; details hold resolved names (folder_path, from_name, to_name).
  actor_id?: string | null;
  family_id?: string | null;
  document?: { id: string; name: string | null; version_no: number | null; deleted: boolean } | null;
  folder?: { id: string; path: string } | null;
  actor_name: string | null;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
  created_at: string;
}

export interface Stats {
  documents_by_status: Partial<Record<DocumentStatus, number>>;
  questions_total: number;
  answered: number;
  not_found: number;
  verified_answers: number;
  needs_review_answers: number;
  feedback_up: number;
  feedback_down: number;
  open_review_items: number;
  older_versions: number; // versions kept that are not the current one
  in_trash: number;
  pages_this_month: Record<PageRoute, number>; // pages read this month, by route (PRD 2, R6); 0 when none
  // PRD 5 U9: this month's OCR pages by who read them (read_again: pages read again), and the
  // scan tokens. No money: org admins never see it (Tushar, 15 Oct 2026).
  reader_pages?: { gemini: number; local: number; cached: number; read_again: number };
  scan_tokens?: { input_tokens: number; output_tokens: number };
}

/** The signed-in user's own story about a document's family (GET / PUT /documents/{id}/my-story).
 * Only its author ever gets it. The user's text: shown as text only, never logged. */
export interface MyStory {
  family_id: string;
  story: string;
  embedded: boolean; // it has a vector of today's model (search can use it)
  created_at: string;
  updated_at: string;
}

// --- The platform operator (super admin, PR 6). Not a customer user. -----------

export interface OperatorMe {
  operator: { id: string; email: string; name: string };
  session: { expires_at: number }; // Unix seconds; the session lasts 30 minutes
}

// POST /operator/2fa/move/start: the new device's key; nothing is stored until it is confirmed.
export interface OperatorMoveOffer {
  move_token: string; // lasts 10 minutes, and only for this session
  secret: string; // base32, in groups of 4
  otpauth_uri: string; // the same key as an otpauth:// link, for a QR code
}

export type OrgStatus = "active" | "disabled" | "deleting";

/** One organisation in GET /operator/orgs: its metadata and counts only. */
export interface Organisation {
  id: string;
  name: string;
  status: OrgStatus;
  disabled_at: string | null;
  delete_after: string | null; // when it is deleted for good (status "deleting")
  created_at: string;
  users: { admins: number; reviewers: number; members: number };
  documents: number;
  pages: number;
  storage_bytes: number;
  last_activity_at: string | null;
  autofile_granted: boolean; // feature 28: auto-filing allowed; its admin switches it on
  autofile_on: boolean;
}

export interface ModelRateUser {
  id: string;
  email: string;
  rpm: number | null;
}

export interface OrgModelRates {
  organisation_rpm: number | null;
  users: ModelRateUser[];
}

/** POST /operator/orgs: the temporary password is in this one reply only. */
export interface OrganisationCreated {
  organisation: { id: string; name: string; admin: { email: string; name: string } };
  temporary_password: string;
}

/** One admin of one organisation (GET /operator/orgs/{id}/admins). Never members or reviewers. */
export interface OrgAdmin {
  id: string;
  name: string;
  email: string;
  two_step: boolean;
  locked: boolean;
  disabled: boolean;
  must_change_password: boolean;
  last_sign_in_at: string | null;
  created_at: string;
}

export type AdminChange = "disable" | "enable" | "unlock" | "reset-2fa";

/** POST /operator/orgs/{id}/admins: the temporary password is in this one reply only. */
export interface AdminCreated {
  admin: { id: string; email: string; name: string };
  temporary_password: string;
}

/** One row of GET /operator/activity: no details, target id, person or document. */
export interface PlatformEvent {
  source: "organisation" | "platform";
  at: string;
  action: string;
  organisation_id: string | null;
  organisation_name: string | null;
  target_type: string | null;
  by: "user" | "operator" | "system";
}

type Counts = Record<string, number>;

/** GET /operator/health: counts only. */
export interface PlatformHealth {
  organisations: {
    id: string;
    name: string;
    status: OrgStatus;
    documents_by_status: Counts;
    failed_by_code: Counts;
    in_progress: number;
    oldest_in_progress_seconds: number | null;
  }[];
  totals: { documents_by_status: Counts; failed_by_code: Counts; in_progress: number; oldest_in_progress_seconds: number | null };
  purges: { purged: number; last_purged_at: string | null; swept: number; last_swept_at: string | null };
  // PRD 5 U9: each worker's reader status, from its reports of the last 10 minutes (none
  // for 2 minutes shows "not reporting"; failing shows "slow"), and the jobs waiting in each
  // lane (null when the queue did not answer). Status only: never an error, key or customer.
  reader?: ReaderReport[];
  queues?: { express: number; main: number } | null;
}

export type ReaderState = "working" | "failing" | "down" | "key_problem";

export interface ReaderReport {
  worker: string;
  state: ReaderState;
  since: string | null; // ISO 8601: in this state since
  seen_at: string; // ISO 8601: its last report
}

// --- Model choice and AI spend (council 11 Oct 2026). The operator sets each
// organisation's models and budget; the organisation's admin sees them read-only. -----

/** The kinds of work a model call is billed as (backend kaagaz/ai/usage.py KINDS). */
export type UsageKind =
  | "scan"
  | "upgrade"
  | "card"
  | "invoice"
  | "map"
  | "embed"
  | "chat"
  | "memory"
  | "search"
  | "story"
  | "careful" // Dynamic's careful re-reads (feature 26)
  | "ensemble_scan" // Ensemble's other readers (feature 27)
  | "ensemble_chat"; // Ensemble's other answer drafts (feature 27)

/** Tokens of some model calls: all an org admin sees (no money, Tushar, 15 Oct 2026). */
export interface UsageTokens {
  calls: number;
  cached_calls: number; // pages taken from the page cache, at no cost
  input_tokens: number;
  output_tokens: number; // thinking included
  reasoning_tokens: number;
}

/** Tokens and US dollars of some model calls (operator only). cost_usd is null when not known. */
export interface UsageTotals extends UsageTokens {
  cost_usd: number | null;
  cost_estimated: boolean; // some of it is the catalogue price times the tokens
  cost_part_unknown: boolean; // some call's cost is not known: cost_usd counts the known ones only
}

export interface UsageByUser extends UsageTokens {
  user_id: string | null; // null: work no user asked for (the upgrade sweep, re-indexing)
  user_name: string | null;
}

export interface UsageByModel extends UsageTokens {
  model: string;
  model_name: string;
}

export interface UsageByKind extends UsageTokens {
  kind: UsageKind;
}

export interface UsageByDay extends UsageTokens {
  day: string; // YYYY-MM-DD in the server's TIMEZONE
}

export interface UsageTopDocument extends UsageTokens {
  document_id: string | null; // null: the document was deleted
  name: string; // "Deleted document" when it was deleted
}

/** This month's spend against the operator's budget (warn only: nothing stops at 100%). Operator only. */
export interface UsageBudget {
  ai_budget_usd: number | null;
  spent_this_month_usd: number;
  spent_part_unknown: boolean; // some of this month's costs are not known (not counted)
  share: number | null; // spent / budget; null without a budget
}

/** GET /admin/usage: one organisation's AI use from `from` to `to` (days in TIMEZONE, both included). */
export interface UsageReport {
  from: string; // YYYY-MM-DD
  to: string;
  totals: UsageTokens;
  by_user: UsageByUser[];
  by_model: UsageByModel[];
  by_kind: UsageByKind[];
  by_day: UsageByDay[];
  top_documents: UsageTopDocument[]; // at most 10, the costliest first
  ai_enabled?: boolean; // false: keyless mode (no OpenRouter key, MODEL_ENABLED=false), no model is called
}

/** One catalogue model (GET /operator/models), from OpenRouter's live list. */
export interface OperatorModel {
  id: string;
  name: string;
  price_in_usd_per_m: number;
  price_out_usd_per_m: number;
  providers: string[]; // its zero-data-retention providers, in OpenRouter's order
  efforts: string[]; // the thinking efforts it accepts
  expires_on: string | null; // YYYY-MM-DD
  eligible: boolean; // passes the catalogue filter today
  seen_at: string; // when the last refresh saw it
  verified: boolean; // verify-models passed for the reader's current prompt
  verified_at: string | null;
  tokens_per_page_in: number | null; // measured by verify-models on its small test page
  tokens_per_page_out: number | null;
  ledger_tokens_in: number | null; // MC3: a scanned page's average over the last 30 days; null: none
  ledger_tokens_out: number | null;
  // 0031: the real cost OpenRouter reported over the last 30 days, every organisation's
  // ledger together. *_costed: those with a reported cost; cost null: none reported.
  scan_pages: number;
  scan_pages_costed: number;
  scan_cost_usd: number | null;
  scan_cost_unknown_calls: number;
  answer_questions: number;
  answer_questions_costed: number;
  answer_cost_usd: number | null;
  answer_cost_unknown_calls: number;
  enabled_at: string | null; // null: not enabled on the platform
  chat_eligible: boolean; // may answer questions (features 25-27, C11)
  chat_verified: boolean; // verify-models passed for the current answer prompt
  vendor_allowed: boolean; // its maker is in OPENROUTER_MODEL_VENDORS; else never used
}

/** A thinking level, as the operator console says it (High / Medium / Low). */
export type AiLevel = "low" | "medium" | "high";

/** One organisation's own AI settings or the platform defaults (features 25-27, C10). For an
 * organisation, an empty list or a null field follows the platform defaults; for the
 * platform, an empty list uses the server's model and a null scan level the server's. */
export interface AiValues {
  scan_models: string[];
  scan_default: string | null;
  chat_models: string[];
  chat_default: string | null;
  scan_thinking: AiLevel | null;
  chat_levels: AiLevel[] | null;
  chat_default_level: AiLevel | null;
  dynamic: boolean | null;
}

/** What the server uses when the platform defaults name no model or scan level. */
export interface AiEnv {
  scan_model: string;
  chat_model: string;
  scan_thinking: string;
}

/** GET /operator/orgs/{id}/ai (operator only: the budget is in US dollars). */
export interface OrgAiReply {
  own: AiValues;
  defaults: AiValues;
  env: AiEnv;
  ensemble: boolean;
  max_agents: number; // the most models that read or answer together
  ai_budget_usd: number | null;
}

/** GET /operator/ai-defaults: the defaults and how many organisations follow each area. */
export interface AiDefaults {
  defaults: AiValues;
  env: AiEnv;
  orgs_inheriting: Record<"scan_models" | "chat_models" | "scan_thinking" | "chat_levels" | "dynamic", number>;
}

/** One row of GET /operator/usage: one organisation's use of one model. No user or document. */
export interface OperatorUsageRow extends UsageTotals {
  organisation_id: string;
  organisation_name: string;
  model: string;
}

// --- Operator insights (council 11 Oct 2026, F1-F4). Counts, sums, rates and durations
// per organisation or model only; null where the database suppressed a value below K
// (or a p95 below 20) or where no cost is known. Days are YYYY-MM-DD in TIMEZONE. -----

/** F1: one organisation's day; only days with something are sent. */
export interface InsightDay {
  organisation_id: string;
  day: string;
  uploads: number;
  sign_ins: number;
  questions: number;
  corrections: number;
  runs: number;
  runs_failed: number;
  pages: number;
  scan_pages: number; // scan pages read by a model (not from the cache)
  cached_pages: number;
  cost_usd: number | null; // every kind; null: no cost known
  cost_scan_usd: number | null;
  cost_documents_usd: number | null; // scan, upgrade, card, invoice, map, embed
  cost_questions_usd: number | null; // chat, memory, search
  cost_estimated: boolean;
  cost_part_unknown: boolean;
  // 0032, from the ledger: pages a model read first (scan, upgrade, not cached) and read
  // again (careful, ensemble_scan), the first reads with a reported cost, and only the cost
  // OpenRouter reported (null: none); calls with no reported cost are counted apart.
  pages_read: number;
  pages_reread: number;
  pages_read_costed: number;
  cost_real_usd: number | null;
  cost_scan_real_usd: number | null;
  cost_answer_real_usd: number | null;
  cost_unknown_calls: number;
  scan_unknown_calls: number;
  answer_unknown_calls: number;
}

/** F2: one organisation's health now. */
export interface OrgInsight {
  organisation_id: string;
  active_users_7d: number;
  active_users_30d: number;
  active_users_prev_30d: number;
  answers_30d: number;
  not_found_30d: number | null; // null: fewer than K answers
  open_review: number;
  oldest_open_review_at: string | null; // ISO 8601
  ai_budget_usd: number | null; // null: no budget
  month_cost_usd: number | null; // this calendar month; null: no cost known
  month_cost_estimated: boolean;
  month_cost_part_unknown: boolean;
  last_active_day: string | null; // ISO date: the latest local day with an upload, question or run; null: never
}

/** GET /operator/insights?days= */
export interface InsightsReply {
  from: string;
  to: string;
  previous_from: string; // the period before: previous_from to the day before from
  ai_enabled: boolean; // false: keyless mode, no model is called
  month: { from: string; today: string; days_in_month: number };
  organisations: OrgInsight[];
  daily: InsightDay[]; // previous_from (or month.from, when earlier) to to
}

/** F3: reading, answers and review of one organisation, or the platform (organisation_id null). */
export interface QualityRow {
  organisation_id: string | null;
  runs: number;
  runs_failed: number;
  runs_failed_platform: number;
  runs_degraded: number;
  documents_run: number;
  pages_cells: number;
  pages_text_layer: number;
  pages_ocr: number;
  pages_blank: number;
  pages_ai: number;
  pages_local: number;
  pages_cached: number;
  pages_unreadable: number;
  ready_documents: number;
  ready_p50_seconds: number | null;
  ready_p95_seconds: number | null; // null: fewer than 20 documents
  answers: number;
  answered: number | null;
  not_found: number | null;
  unchecked: number | null;
  answers_verified: number | null;
  answers_fixed_query: number | null;
  citations: number | null;
  feedback: number;
  feedback_up: number | null;
  values_total: number;
  values_needs_review: number | null;
  corrections: number;
  corrections_second: number | null;
  corrections_typed: number | null;
}

/** GET /operator/quality?days= */
export interface QualityReply {
  from: string;
  to: string;
  ai_enabled: boolean;
  platform: QualityRow | null; // null: no platform row
  organisations: QualityRow[];
}

/** F4: one model, observational (not an A/B test). No organisation. */
export interface BoardRow {
  model: string;
  documents: number;
  runs: number;
  runs_failed: number;
  runs_degraded: number;
  pages_ai: number;
  pages_local: number;
  pages_cached: number;
  seconds_per_page_p50: number | null;
  seconds_per_page_p95: number | null;
  calls: number;
  scan_calls: number;
  cached_scan_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null;
  scan_cost_usd: number | null;
  cost_estimated: boolean;
  cost_part_unknown: boolean;
  ocr_pages: number;
  pages_disagree: number | null; // page signals: null when documents < K
  pages_refused: number | null;
  pages_fell_back: number | null;
  pages_unpinned: number | null;
}

/** GET /operator/model-board?days= */
export interface BoardReply {
  from: string;
  to: string;
  ai_enabled: boolean;
  items: BoardRow[];
}

// --- Word-level review and Ensemble after the scan (scan review UX rules, 16 Oct 2026;
// backend/kaagaz/api/routers/word_review.py and ensemble.py, views.run_json) ---

export type Band = "check" | "review";
export type FlagState = "open" | "reviewed" | "resolved" | "in_ensemble" | "proposed";
export type EnsembleScope = "word" | "words" | "flagged" | "document";

/** ``Run``: one Ensemble run's progress, counts and error. */
export interface EnsembleRun {
  id: string;
  scope: EnsembleScope;
  status: "queued" | "running" | "done" | "stopped" | "failed";
  pages_total: number;
  pages_done: number;
  page_now: number | null; // while running
  words_total: number;
  agreed: number;
  changed: number;
  unresolved: number;
  kept: number;
  failed_pages: number[];
  started_at: string | null;
  finished_at: string | null;
  error: { code: string; message: string } | null; // message: what happened, what it means
}

/** GET /documents/{id}/review-status */
export interface ReviewStatus {
  pages: number;
  words: number;
  flagged: number;
  by_band: Record<Band, number>;
  reviewed: number;
  resolved: number;
  remaining: number;
  last_change_at: string | null;
  status: DocumentStatus;
  summary: string | null; // "Scan complete · 14 pages · 2,431 words · 12 flagged for review"; null until ready
  export: { ready: boolean; message: string };
  can_review: boolean; // the Reviewer check: confirm, edit, undo, Ensemble
  ensemble: { allowed: boolean; reason: string | null; run: EnsembleRun | null };
}

/** ``WordFlag``: one flagged word (GET /documents/{id}/flags, and the confirm or edit reply). */
export interface WordFlag {
  word_key: string;
  kind: "mark" | "line" | "value";
  page_no: number;
  region: Region;
  as_read: string;
  text: string; // the text now
  band: Band;
  reason: string;
  reason_text: string; // "smudged print"
  score: number | null; // only when something measured it
  alternatives: string[] | null; // null for a member
  value_id: string | null;
  state: FlagState;
  ensemble: {
    reading_id: string;
    run_id: string;
    outcome: "agreed" | "changed" | "unresolved" | "skipped_user_edit";
    proposal: string | null;
    decision: "kept" | "reverted" | null;
  } | null; // reviewers only
  change: { id: string; action: string; at: string | null; by_you: boolean } | null;
}

/** ``WordChange``: one row of the history. */
export interface WordChange {
  id: string;
  word_key: string;
  kind: string;
  page_no: number;
  region: Region;
  action: string; // confirm, edit, resolve, keep, ...
  before_text: string;
  after_text: string;
  by: "person" | "ensemble";
  by_you: boolean;
  user_name: string | null;
  created_at: string | null;
  undone_at: string | null;
  on_page: boolean;
  note: string | null; // "No longer on the page"
  can_undo: boolean;
}

export interface EnsembleEstimate {
  pages: number;
  words: number;
  seconds: number;
}

/** AI settings by task (spec 17 Oct 2026): levels, never model names. */
export type AiTask = "scan" | "ask" | "ensemble" | "autofile";
export type TaskLevel = "none" | "low" | "high" | "max" | "off" | "on";

export interface AiTaskSetting {
  task: AiTask;
  effective: TaskLevel;
  mine: TaskLevel | null; // null: the organisation's setting
  organisation: TaskLevel | null;
  locked: boolean;
  allowed: TaskLevel[];
  available: boolean; // false: switched off for the whole product
}

/** GET /ai-settings, and both PUTs' reply. */
export interface AiSettings {
  tasks: AiTaskSetting[];
  can_edit_organisation: boolean;
}

/** GET /operator/feature-switches: one row per product feature. */
export interface FeatureSwitch {
  feature: string;
  enabled: boolean;
  organisations_using: number;
}

/** GET /operator/orgs/{id}/feature-switches (0039): one organisation's own switch, with the platform's. */
export interface OrgFeatureSwitch {
  feature: string;
  enabled: boolean;
  platform_enabled: boolean; // false: off for every organisation, whatever this one says
}

/** GET /operator/session-timeout and /operator/orgs/{id}/session-timeout (0040): hours with no
 * activity before signing in again, 1 to 24. null: the default (the platform's) or follow the
 * platform (an organisation's); platform_hours only on an organisation's. */
export interface SessionTimeout {
  hours: number | null;
  platform_hours?: number | null;
  default_hours: number;
}

/** GET /operator/test-model: the whole platform on one model for a test, or not. */
export interface TestModel {
  active: boolean;
  scan_model: string | null;
  chat_model: string | null;
  started_at: string | null;
  since: { pages: number; questions: number; cost_usd: number | null; avg_seconds_per_page: number | null } | null; // null when none runs
  note: string | null;
}
