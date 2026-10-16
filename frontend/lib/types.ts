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
}

/** GET /api/search (PRD 3 Q2): the documents found, best first, at most SEARCH_MAX_DOCUMENTS. */
export interface DocumentSearch {
  documents: FoundDocument[];
  story_matches?: unknown; // checked by lib/storyMatches.ts before use
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
}

// A folder of this customer. The API sends the flat list; the screen builds the
// tree (components/documents/folderTree.ts). Folder and tags belong to the
// document's family: every version shares them.
export interface Folder {
  id: string;
  name: string;
  parent_id: string | null; // null: at the top level
  created_at: string;
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
  ensemble?: EnsembleOffer | null; // "More than one reader…" (feature 27); null: not allowed here
}

/** What an upload or a whole document's Read again asks for (features 26, 27); nothing: the default. */
export interface UploadReading {
  read_mode?: "dynamic" | "ensemble";
  read_model?: string;
  read_agents?: string[];
}

/** The models an upload may read with together (feature 27), by name; main is the main reader. */
export interface EnsembleOffer {
  readers: { id: string; name: string }[];
  max: number;
  main: string;
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
  // number is given; one sentence asks for Review and closest_documents says where.
  status: "answered" | "not_found" | "unchecked";
  label: Label | null; // null when not found
  sentences: AnswerSentence[];
  citations: Citation[];
  closest_documents: { id: string; display_name: string }[];
  route: "fixed_query" | "search";
  normalized_question: string;
  model_used: boolean;
  rewritten_question?: string | null; // PRD 3 H1: what a follow-up was searched as; missing before PRD 3
  understood_words?: { word: string; meaning: string }[]; // my words chat used (A3); missing before it
  // PRD 5 F6, missing before it: "Not found in the readable text. 2 pages couldn't be read: ..."
  // on a not-found answer, or "Part of this text was hard to read." on an answer that needs
  // review; null otherwise. The pages it names, and how many there are in all.
  reading_note?: string | null;
  unreadable_pages?: UnreadablePage[];
  unreadable_page_count?: number;
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
  feedback: "up" | "down" | null;
}

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

/** One model an upload may read scans with (GET /upload-limits readers), the default first. */
export interface ReaderOption {
  id: string; // the OpenRouter model ID, sent as read_model
  title: string;
  hint: string; // "about $X a page"
  available: boolean;
}

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
  daily: InsightDay[]; // previous_from to to
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
