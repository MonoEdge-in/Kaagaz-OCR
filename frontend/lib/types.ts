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

/**
 * The file's type: at upload, the one sniffed from its first bytes (pdf, png, jpeg, tiff, heic,
 * zip, ole2, text); once the worker has checked it, its kind (#522, the backend's resolve.py).
 */
export type DetectedType =
  | "pdf"
  | "png"
  | "jpeg"
  | "tiff"
  | "heic"
  | "zip"
  | "ole2"
  | "text"
  | "xlsx"
  | "xls"
  | "csv"
  | "docx"
  | "dotx"
  | "doc"
  | "pptx"
  | "ppt"
  | "txt"
  | "md"
  | "json"
  | "html";

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
  | "local_reader" // PRD 5 Q7: read by the backup reader while the main reader was down
  | "mixed_digits" // #524: an amount's digits are of two scripts (९४0000/-)
  | "amount_commas" // #524: an amount's commas fit no grouping (1,15,0000)
  | "amount_mark" // #524: a mark inside an amount, or "/-" read as a 1 (115000001-)
  | "amount_outlier"; // #524: a total more than ten times the page's other total

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
    totp_enabled: boolean; // two-step sign-in is on (required for an admin unless they turned it off)
    two_step_turned_off: boolean; // an admin turned their own two-step off (Tushar, 30 Oct 2026)
    two_step_required?: boolean; // the super admin requires it for this organisation's admins (#481)
    can_delete_forever: boolean; // an admin allowed this member or reviewer (never an admin)
    email_confirmed: boolean; // false: the "Confirm your email" banner (#457; true while email is off)
    // #507: the organisation asks this person to sign in with Google and they have no connected
    // account that passes its rules yet: every route but Ways to sign in replies 403
    // google_link_required, so the app sends them there (lib/session.tsx).
    google_link_required?: boolean;
    // #507 review: in that session, the connected account is from before its domain could be
    // checked, so the banner asks for one Google sign-in instead of a new connection.
    google_unchecked?: boolean;
  };
  customer: { id: string; name: string };
  session: { scope: SessionScope };
  // The sign-in providers switched on ("google"), for every role (#457 review): empty while none
  // is, and Ways to sign in is hidden; the copy names only these.
  providers: string[];
  // Ways to sign in (#457 phase 1): approved connections made in the last 14 days, for the
  // app's "Google connected on 30 Oct." banner. Empty while no provider is on (always sent).
  recent_connections?: { provider: string; linked_at: string }[];
  // PRD 7 (#601): Management, and the departments this person heads. Older servers leave it out.
  access?: { management: boolean; heads: string[]; in_department?: boolean }; // in_department: PR 10c
}

/** One connected account of the signed-in user (GET /auth/identities). */
export interface ConnectedIdentity {
  id: string;
  provider: string; // "google"
  email: string | null; // the account's email when it was connected, a label only
  status: "pending" | "approved";
  linked_at: string;
  last_used_at: string | null;
}

/** Account, Ways to sign in: the providers switched on, and the user's own connections. */
export interface WaysToSignIn {
  providers: string[];
  identities: ConnectedIdentity[];
  // #507: the organisation's rules for this person: Google only for their role, and the
  // company domains a Google account must be from (empty: any).
  rules?: { google_only: boolean; domains: string[] };
}

/** Admin, People, Connections to approve (GET /admin/identities). */
export interface PendingConnection {
  id: string;
  user_id: string;
  display_name: string;
  user_email: string;
  provider: string;
  email: string | null;
  linked_at: string;
}

/** "Turn on" two-step sign-in (POST /auth/2fa/start, or after a right password). Nothing is stored yet. */
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
  folder_id: string | null; // null: at the top level, in no folder, or in one this person can't see
  // PRD 7 (#601): in a folder this person can't see (folder_id null): "Shared with you", never a path.
  shared_with_you?: boolean;
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
  // Issue 407: still being read 6 hours after it was uploaded or last read again, so it may be read again (Retry).
  stuck?: boolean;
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
  // #522: a zip only: where it was unpacked, null until it is (or when it is kept as a file).
  // Its status_message already says "Unpacked: 120 files into folder "X", 3 skipped (...)".
  unpack?: ZipUnpack | null;
}

/** #522: a zip upload unpacked into a folder by the server: its files counted, and the first
 * skipped ones, each with why in plain words. */
export interface ZipUnpack {
  folder_upload_id: string;
  folder_id: string | null; // null once that folder is deleted
  folder_name: string;
  files: number; // every file in the zip but a Mac's own (__MACOSX, .DS_Store)
  unpacked: number;
  already_in: number; // duplicates: already in Kaagaz, nothing new stored
  skipped: number;
  waiting: number; // still to unpack
  skipped_files: ZipSkippedFile[]; // the first 20, in the zip's order
}

export interface ZipSkippedFile {
  name: string;
  code: string;
  reason: string; // plain words, safe to show as is
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
  text_overlap_estimated?: boolean; // #521: true when text_overlap is the estimate, not counted exactly
  name_similarity: number | null;
  shared_identifier: string | null; // its kind, such as "survey_number"
  reasons?: string | string[] | null;
}

/** GET /api/chat/ready-questions: Home's questions for this person, their own habits first;
 * ``ready`` when the answer waits made, so pressing it answers at once. */
export interface ReadyQuestion {
  text: string;
  yours: boolean;
  ready: boolean;
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
  pages: {
    page_no: number;
    changes: number;
    truncated: boolean;
    runs: CompareRun[];
    // #521: the words changed (a replacement counts its longer side) and each side's words.
    words_changed?: number;
    left_words?: number;
    right_words?: number;
  }[];
  truncated?: boolean; // over the compare limits: only the first part is compared
  overview?: string; // #521: "94% the same. 38 words changed on pages 2 and 5. Page 6 is new."
  left_pages?: number | null; // #521 review: each side's page count, for its page images
  right_pages?: number | null;
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
  capped?: boolean; // true when more documents matched than the cap let through (issue 374)
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
  department_only?: boolean; // PRD 7 (#601): a department kept to its people
  private?: boolean; // PRD 7 (#601): only the people named on it (and its head, Management) see it
}

/** #542: a folder in Trash, with what it holds at every depth (files: a file's versions count once). */
export interface TrashedFolder {
  id: string;
  name: string;
  kind: FolderKind;
  was_in: string[] | null; // the live folder it goes back into, root first; [] the top level; null gone
  files: number;
  folders: number;
  deleted_at: string;
  deleted_by_name: string | null;
  can_restore: boolean; // an admin, or who uploaded every version of every file in it
}

/** #542: a folder in Trash opened: itself, its path within Trash (Trash's item first), its folders. */
export interface TrashedFolderContents {
  folder: TrashedFolder;
  path: { id: string; name: string }[];
  folders: TrashedFolder[];
}

/** #542: the folder that came back with a file restored from a folder in Trash (restoreDocument's reply). */
export type FolderBack = Omit<FolderRestored, "files" | "folders">;

/** #594: where a restored file went because its folder no longer exists (restoreDocument's reply):
 * held for the person until it is filed, or into `folder` (null: one they can't see). */
export interface FilePlaced {
  held: boolean;
  folder: Folder | null;
}

/** #542: a restored folder, where it went ([] the top level), and why it is not as it was. */
export interface FolderRestored {
  folder: Folder;
  location: string[] | null; // PRD 7: null when a folder on its path is one you can't see
  renamed: boolean; // a folder there had its name: it is "Name (2)" now
  relocated: boolean; // its folder no longer exists: it is at `location` instead
  sealed: boolean; // #594: a department or private folder it was in no longer exists: it came back private
  files: number;
  folders: number;
}

/** #537: what a folder holds at every depth: live files (a file's versions count once), folders, bytes. */
export interface FolderSummary {
  files: number;
  folders: number;
  bytes: number;
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
  page_turns: PageTurnSaved[]; // pages turned for everyone (PRD 7 section 14, #600); a page not listed is not turned
}

/** A page turned for everyone: clockwise degrees on top of the file's own turn. */
export interface PageTurnSaved {
  page_no: number; // from 1
  turn: 90 | 180 | 270;
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
  zip_bytes?: number; // #522: the folder upload limit; left out by an older server
  max_pages: number; // for a PDF's pages and a TIFF's frames
  readers: UploadReader[]; // the scan readers an upload may choose, the server's default first
  // #484: this organisation's folder upload limits; left out by an older server (no folder upload).
  folder_upload?: FolderUploadLimits;
  // #484: "Let Kaagaz sort files" is offered (auto-filing is on for this person).
  sort_available?: boolean;
}

/** The most one folder upload may hold here (the organisation's, else the platform's, else the
 * server's), how many sends a minute the server takes from this tab, and how many at once. */
export interface FolderUploadLimits {
  max_files: number;
  max_bytes: number;
  max_folders: number;
  max_depth: number; // 100
  calls_per_minute: number;
  files_at_once: number; // the organisation's sends at once; the browser sends at most 4
}

/** POST /folder-uploads: the folders to make, parents first (entry 0 the root), and the counts. */
export interface FolderUploadPlanBody {
  id: string; // made by the browser
  user_id: string;
  target: "department" | "folder";
  parent_folder_id: string | null; // "folder": the folder it goes in, null for the top level
  merge: boolean; // false: "Keep separate", the root becomes "A (2)"
  folders: { name: string; parent: number | null }[];
  file_count: number;
  total_bytes: number;
  skipped_count: number;
  only_new?: boolean; // "Add new files only": the counts are checked against the ceilings, not the organisation's limits
}

/** The plan's reply: folder_ids[i] is the Kaagaz folder for manifest folder i. */
export interface FolderUploadPlan {
  id: string;
  root_folder_id: string;
  root_name: string;
  folder_ids: string[];
  created: number;
  merged: number;
}

/** One file to register: its id within the upload, its folder, and why the browser refused it. */
export interface NewFolderItem {
  path_key: string; // SHA-256 hex of its NFC path, root first
  name: string;
  folder_id: string;
  size: number;
  refused?: "unsupported_type"; // the server judges size, emptiness and names itself
  sha256?: string; // "Add new files only": the SHA-256 (hex) of its bytes; a match comes back "duplicate", never sent
}

export type FolderItemFailure = "too_large" | "unsupported_type" | "empty_file" | "folder_gone" | "bad_name";
export type FolderItemView = "waiting" | "attention" | "duplicate" | "all";

/** One file of a folder upload, as the server keeps it; its folder never changes. */
export interface FolderUploadItem {
  id: string;
  seq: number;
  path_key: string;
  name: string;
  folder_id: string;
  size: number;
  state: "waiting" | "stored" | "duplicate" | "failed";
  failure_code: FolderItemFailure | null;
  document_id: string | null;
  // The stored (or matched) document; null with none, or one deleted for good.
  document: {
    status: DocumentStatus;
    failure_code: string | null;
    folder_id: string | null;
    in_trash: boolean;
  } | null;
  hidden?: boolean; // PRD 7 (#599): a copy of a file this person can't see; document_id and document are null
}

/** GET /folder-uploads/previous: this person's latest upload of a folder of that name whose folder is still there. */
export interface FolderUploadPrevious {
  id: string;
  target: "department" | "folder";
  parent_folder_id: string | null;
  root_folder_id: string;
  created_at: string;
}

/** GET /folder-uploads/{id}/items: a page, and where the next starts (null: the last page). */
export interface FolderItemsPage {
  items: FolderUploadItem[];
  next_after: number | null;
}

/** GET /folder-uploads/{id}: one folder upload's counts, by state, and of its documents. In the
 * list (GET /folder-uploads) reading, ready, problem and held are null. */
export interface FolderUploadSummary {
  id: string;
  root_folder_id: string | null;
  root_name: string; // the Kaagaz folder, "A (2)" after Keep separate
  source_name: string; // the picked folder's own name, "A"
  target: "department" | "folder";
  parent_folder_id: string | null;
  merge: boolean;
  created_at: string;
  file_count: number;
  total_bytes: number;
  skipped_count: number;
  registered: number; // items listed so far
  waiting: number;
  stored: number;
  duplicate: number;
  failed: number;
  reading: number | null; // stored, still being checked or read
  ready: number | null;
  problem: number | null; // stored, but its reading failed or was refused
  held: number | null; // stored, waiting for the reading pause (#478), not counted in reading
  held_message: string | null; // the pause's own words while any is held, as a held document's
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

export type UploadResult =
  | {
      document: DocumentSummary;
      duplicate: boolean;
      in_trash: boolean; // a duplicate of a document that is in Trash
      hidden?: false;
    }
  // PRD 7 (#599): the file is already in Kaagaz, in a place this person can't see. Nothing more.
  | { document: null; duplicate: true; in_trash: false; hidden: true };

export interface SheetCells {
  page_no: number;
  total_rows: number;
  rows: { row: number; cells: { column: number; text: string }[] }[];
}

/** GET /documents/{id}/words (package H): the lines holding the words on every page (at most 2,000), and every page with one. */
export interface DocumentWords {
  boxes: Region[];
  pages: number[];
  /** For each box, the first of the words its line holds: its colour (24 Oct 2026). */
  hits?: number[];
  /** Each word as the server cleaned it, in order: how many lines hold it, on which pages. */
  found?: WordFound[];
}

export interface WordFound {
  word: string;
  lines: number;
  pages: number[];
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
  key?: string; // #527: the clean copy's lines only, the line's stable key (a flag's word_key)
  correction?: LineCorrection | null; // #527: the clean copy's lines only; `text` is then the corrected text
}

/** #527: the correction in force for a clean copy line; `before_text` is the text it replaced. */
export interface LineCorrection {
  before_text: string;
  by: string; // the person's name
  at: string;
  change_id: string; // for Undo (POST changes/{id}/undo)
}

/** POST /documents/{id}/lines/{key}/edit (#527): the change and the line as it now reads. */
export interface LineSaved {
  change_id: string;
  line: ReadLine;
  written: { pieces: number; values: number }; // the search pieces and values that took the new text
  skipped: { pieces: number; values: number }; // those over the line that did not (not there exactly once)
}

/** GET /documents/{id}/clear-copy (Read clearly): every page's main reader lines, in order. */
export interface ClearCopy {
  pages: { page_no: number; lines: ReadLine[]; marks: ReadingMark[] }[];
  truncated: boolean; // the document-wide cap was reached: the rest was left out
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

/** PRD 6 A: a document listed under an answer. `why` is code-written words, never a digit;
 * `citations` are its sources' numbers (none for a not-found answer's closest document). */
export interface ListedDocument {
  document_id: string;
  title: string;
  why: string;
  citations: number[];
}

export interface Answer {
  /** KZ-006 (24 Oct 2026): every model was down; said as such, never as "not found". */
  outage?: boolean;
  /** #535: the outage was a draft that took too long ("timeout"), so asking again won't help. */
  outage_reason?: "timeout";
  // "unchecked": the figure asked for is only in unchecked card values, so no
  // number is given; closest_documents says where.
  // "no_access" (PRD 7, #602): a saved answer that names a file the person can no longer see,
  // replaced whole by the server: one sentence (NO_ACCESS_TEXT), every list empty.
  status: "answered" | "not_found" | "unchecked" | "clarify" | "no_access";
  // PRD 6 A, answer contract v2 (24 Oct 2026); all four missing on answers saved before it,
  // which render as they did. The opening and assumptions are code-written lines.
  version?: 2;
  opening?: string | null;
  documents?: ListedDocument[];
  assumptions?: string[];
  label: Label | null; // null when not found
  sentences: AnswerSentence[];
  citations: Citation[];
  closest_documents: { id: string; display_name: string }[];
  route: "fixed_query" | "search" | "small_talk"; // small_talk: "hi", "what can you do" (24 Oct 2026)
  /** Questions to ask with one press: after small talk, or when nothing was close (24 Oct 2026). */
  suggestions?: string[];
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
  missing_words?: string[]; // #410: a not-found's words no document searched has; only when there is one
  // Features 25-27 (C7), only on an answer drafted with the asker's answer settings: the
  // names (and ids) of the models that replied, the one used first; how many were asked;
  // whether the others confirmed it (null with one model); the thinking level.
  agents?: string[];
  agent_ids?: string[];
  agents_asked?: number;
  agreement?: "agreed" | "not_confirmed" | null;
  thinking?: ThinkingLevel;
  // PRD 6 B (answer contract v2), absent on older answers: the question asked instead of an
  // answer (status "clarify").
  clarify?: Clarify;
}

/** A clarifying question (PRD 6 B): choices by server id; "other" sends nothing. */
export interface Clarify {
  id: string;
  question: string;
  choices: { id: string; label: string; detail?: string; kind: "pick" | "other" | "all"; document_ids?: string[] }[];
  multi: boolean;
  turn: number;
}

/** POST /chat's `reply`: the open clarify answered by its choices' ids (none: the typed words). */
export interface ClarifyReply {
  message_id: string;
  choice_ids: string[];
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

/** One answer marked Not useful after the notice, on the admin's Answer quality page (PRD 6 F2). */
export interface AnswerQualityItem {
  id: string; // the feedback's id: Mark as handled names it
  message_id: string;
  asked_by: string;
  question: string | null;
  answer: Answer; // as it may be shown now (sources gone since lose their words)
  reason_codes: ReasonCode[];
  reason: string | null;
  given_at: string;
  handled_at: string | null;
}

/** Useful and Not useful in one week, from its Monday (India time). */
export interface WeekCounts {
  week: string;
  useful: number;
  not_useful: number;
}

/** GET /admin/learned (PRD 6 F3): a document Useful marks lift now; no question, no name. */
export interface LearnedDocument {
  document_id: string;
  document_name: string;
  people: number;
  last_marked_at: string;
}

/** GET /admin/answer-quality: one page of items, newest first, and each week of the period, newest first. */
export interface AnswerQualityReply {
  from: string;
  weeks: WeekCounts[];
  items: AnswerQualityItem[];
  total: number;
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
  page_turn: 0 | 90 | 180 | 270; // the page's turn saved for everyone (#600); 0: not turned
}

/** The review queue's reason filter (PRD 3 Y5; PRD 5 U6 adds four, then #524's amount four). */
export type ReviewReason =
  | "check_failed"
  | "readers_disagree"
  | "low_confidence"
  | "single_reader"
  | "unsure_text"
  | "box_unanchored"
  | "local_reader"
  | "ocr_script"
  | "mixed_digits"
  | "amount_commas"
  | "amount_mark"
  | "amount_outlier";

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

/** Why Verify all left a value out: nothing to verify, too long, or it left the queue meanwhile. */
export type VerifyAllSkip = "no_reading" | "too_long" | "left_queue";

/** POST /review/verify-all's reply: how many were verified, left out (and why), and still to review. */
export interface VerifyAllResult {
  verified: number;
  skipped: { reason: VerifyAllSkip; count: number }[];
  left: number;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  role: Role;
  disabled: boolean;
  created_at: string;
  locked_until: string | null; // ISO 8601; null when not locked (a passed lock is sent as null)
  totp_enabled: boolean;
  two_step_turned_off: boolean; // this admin turned their own two-step off
  must_change_password: boolean;
  can_delete_forever: boolean; // an admin allowed Delete forever (never set for an admin)
  // #457 phase 1: "password" and each approved provider. Only the list and the replies that
  // removed connections send it; a row keeps what it had otherwise.
  signs_in_with?: string[];
  // #507: their role signs in with Google only and they have no connected account that passes.
  google_needed?: boolean;
  // #507 review: and their connected account's domain is not checked yet (one Google sign-in does).
  google_unchecked?: boolean;
  // PRD 7 (#601): sees every file. The list also sends their departments (head: they head it).
  management?: boolean;
  departments?: { id: string; name: string; head: boolean }[];
}

/** PRD 7 (#601), GET /admin/users/{id}/access: what one person can see, beyond their role. Paths
 * are an Admin's (every folder name); `added_by` and `given_by` are user ids. */
export interface PersonAccess {
  user: { id: string; name: string; role: Role; management: boolean; disabled: boolean };
  departments: { id: string; name: string; head: boolean; added_at: string; added_by: string }[];
  private_folders: { id: string; name: string; path: string | null }[];
  files: { id: string; name: string; path: string | null; given_at: string; given_by: string | null }[];
}

/** PRD 7 (#601), GET /admin/departments: one department with its access. */
export interface AdminDepartment {
  id: string;
  name: string;
  department_only: boolean;
  members: number;
  heads: Person[];
  warnings: string[]; // "no_members": Department only, with nobody in it
}

/** GET /admin/departments/{id}/members */
export interface DepartmentMember {
  id: string;
  name: string;
  head: boolean;
  added_at: string;
}

/** PRD 7 (#601): who would lose and who would gain a place's files, and how many files it holds. */
export interface PlacePreview {
  losing: Person[];
  gaining: Person[];
  files: number;
  // Make private's preview (PR 10b, coming): the people named on it before, who see it again.
  named_before?: Person[];
}

/** PRD 7 PR 10c (#601), a move's preview: for the person a file is held for, counts only (the lists
 * empty, never anyone's name). */
export interface MovePreview extends PlacePreview {
  losing_count?: number;
  gaining_count?: number;
}

/** PRD 7 PR 10c (#601), PATCH /documents/{id}: an admin moved the file where they can't see it.
 * The move stands. */
export interface HiddenMove {
  id: string;
  hidden: true;
}

/** PRD 7 (#601), GET /{documents|folders}/{id}/audience: who can see it, grouped by why, in the
 * Audience panel's order; `place`: the department (head, member) or the private folder (named). */
export interface Audience {
  groups: { reason: string; place?: { id: string; name: string }; people: Person[] }[];
  everyone_else: number; // how many people of the organisation can't see it
}

/** The same with ?user_id= (Check a person): the reasons in the same order. */
export interface AudienceCheck {
  can_see: boolean;
  reasons: string[];
}

/** PRD 7 (#601), GET /folders/{id}/kept-from-me: an Admin's names list, the files in a folder
 * they can't open (`count`: all of them; `items`: the first 500 by name). */
export interface KeptFromMe {
  count: number;
  items: {
    id: string;
    name: string;
    kind: string; // the detected type
    uploader: string | null;
    created_at: string;
    version_no: number;
    locked: boolean;
  }[];
}

/** PRD 7 (#601), GET /folders/{id}/named: a private folder's list; `can_edit`: may change it. */
export interface NamedList {
  folder: { id: string; name: string; path: string | null };
  named: Person[];
  heads: Person[];
  can_edit: boolean;
}

/** POST /admin/users: the new user; with email on (#457), whether the invite email went. */
export interface AddedUser extends AdminUser {
  invited?: boolean;
}

/** POST /auth/signup with email on (#457): no set-up yet, the email is confirmed first. */
export interface SignUpConfirming {
  status: "confirm_email";
  sent: boolean; // false: the confirm email could not be sent
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
  // #457 phase 1: "Approve new sign-in connections automatically"; left out while no provider is on.
  auto_approve_connections?: boolean;
  // #507: Ways to sign in, per role; left out while Google sign-in is off for the product.
  google?: GoogleRules;
}

/** The admin's Google sign-in rules (#507). available: Google sign-in is on for the organisation
 * (the super admin's switch). */
export interface GoogleRules {
  available: boolean;
  allowed_roles: Role[];
  only_roles: Role[];
  domains: string[];
}

/** PATCH /admin/settings' google: it replaces all three. */
export type GoogleRulesChange = Pick<GoogleRules, "allowed_roles" | "only_roles" | "domains">;

/** Operator: one organisation's Google sign-in (#507). on: null follows the platform. */
export interface OrgGoogleSignIn {
  on: boolean | null;
  platform_on: boolean;
  effective: boolean;
  google_only_roles: Role[];
  domains: string[];
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
  subject_name?: string | null; // a "user" row's person, by name (issue 375); null once removed
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

/** #386: the admin's members table, GET /admin/members?period=. No money, no tokens. */
export type MemberPeriod = "month" | "30d" | "all";

export interface MemberNumbers {
  user_id: string;
  display_name: string;
  email: string;
  role: Role;
  disabled: boolean;
  documents_uploaded: number; // every version, those in Trash too
  pages_read: number; // the pages of those files that were read, each page once
  questions_asked: number; // a clarifying question back not counted
  values_verified: number;
  last_active_at: string | null; // ISO time; null: never signed in
}

export interface MembersReply {
  period: MemberPeriod;
  from: string | null; // the first day counted; null: all time
  to: string; // today
  members: MemberNumbers[];
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

// pending (#457): a password sign-up waiting for the operator's Approve.
export type OrgStatus = "active" | "pending" | "disabled" | "deleting";

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
  admin_email_confirmed: boolean; // #457: one of its admins confirmed their email; Approve needs it
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
  // #246 O1: the commit the API's image was built from ("local": built by hand). The
  // deployer's last attempt and last good release stay in files on the server.
  release?: { commit: string };
  // QA C2 (4 Nov 2026): whether Mistral has stopped reading pages, as the AI settings say it.
  mistral_status?: MistralStatus;
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
  | "ensemble_chat" // Ensemble's other answer drafts (feature 27)
  | "land" // PRD 6 D: reading owners, shares and places of land records
  | "ulc_scan"; // #488: a page read under Ultra low cost

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
  // #478: pages AI read today (India day) and the daily page limit in effect (null: none); never money.
  reading_today?: { pages: number; limit: number | null };
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
  ulc_ready: boolean; // #488: may lead Ultra low cost (a Gemini with a known price that takes minimal)
  ulc_why: string | null; // #488: the one plain reason it can't; null when it can
}

/** A thinking level, as the operator console says it (High / Medium / Low). */
export type AiLevel = "low" | "medium" | "high";
/** A scan's thinking level: Minimal too, for scanning only, never answering (#463). */
export type ScanLevel = "minimal" | AiLevel;
/** How much picture detail a page is sent with (#463). */
export type Resolution = "low" | "high";
/** Who reads scanned pages (#473): Gemini through OpenRouter, or Mistral OCR; or (#488) Gemini
 * read as briefly as possible under a ceiling on what one page may cost. */
export type ScanReader = "openrouter" | "mistral" | "ultra_low_cost";
export type MistralModelId = "mistral-ocr-4-1" | "mistral-ocr-2512";
/** A Mistral OCR model (#473); has_blocks: it says where each line is on the page. */
export interface MistralModel {
  id: MistralModelId;
  name: string;
  usd_per_page: number;
  has_blocks: boolean;
}
/** Whether Mistral has stopped reading pages (#473): code mistral_key for a refused key or no
 * credit, mistral when it isn't answering, mistral_setup when it is chosen but the server isn't
 * set up for it; since when, and the documents waiting for it. */
export interface MistralStatus {
  stopped: boolean;
  code: "mistral_key" | "mistral" | "mistral_setup" | null;
  since: string | null;
  documents_waiting: number;
}

/** One organisation's own AI settings or the platform defaults (features 25-27, C10). For an
 * organisation, an empty list or a null field follows the platform defaults; for the
 * platform, an empty list uses the server's model and a null scan level the server's. */
export interface AiValues {
  scan_models: string[];
  scan_default: string | null;
  chat_models: string[];
  chat_default: string | null;
  scan_thinking: ScanLevel | null;
  scan_resolution: Resolution | null;
  careful_thinking: ScanLevel | null; // pages read again
  careful_resolution: Resolution | null;
  page_max_output_tokens: number | null; // the most a page's read may write and think
  chat_levels: AiLevel[] | null;
  chat_default_level: AiLevel | null;
  dynamic: boolean | null;
  scan_reader: ScanReader | null; // #473
  mistral_model: MistralModelId | null;
  daily_page_limit: number | null; // #478: pages AI may read in one India day; the platform's null: no limit
  ulc_ceiling_paise: number | null; // #488: the most a page may cost under ultra low cost, 20 to 55; null follows (40)
}

/** What the server uses when the platform defaults name no model or scan setting, and
 * whether Mistral is set up on it (#473): mistral_missing says which half is not. */
export interface AiEnv {
  scan_model: string;
  chat_model: string;
  scan_thinking: string;
  scan_resolution: string;
  careful_thinking: string;
  careful_resolution: string;
  page_max_output_tokens: number;
  scan_reader: ScanReader; // #564: ultra_low_cost while OCR_ULTRA_LOW_COST is on
  ulc_ceiling_paise: number; // the server's ceiling for a saved Ultra low cost with none (40)
  switch_ceiling_paise: number | null; // #564: OCR_PAGE_CEILING_PAISE where the switch reads (null: off)
  mistral_model: MistralModelId;
  mistral_ready: boolean;
  mistral_missing: "key" | "zdr" | null;
  mistral_models: MistralModel[];
  mistral_status: MistralStatus;
}

/** GET /operator/orgs/{id}/ai (operator only: the budget is in US dollars). */
export interface OrgAiReply {
  own: AiValues;
  defaults: AiValues;
  env: AiEnv;
  ensemble: boolean;
  max_agents: number; // the most models that read or answer together
  ai_budget_usd: number | null;
  reading_today: { pages: number; cost_usd: number | null }; // #478: today's counter (India day)
}

/** GET /operator/ai-defaults: the defaults and how many organisations follow each area. */
export interface AiDefaults {
  defaults: AiValues;
  env: AiEnv;
  orgs_inheriting: Record<
    | "scan_models"
    | "chat_models"
    | "scan_thinking"
    | "scan_resolution"
    | "careful_thinking"
    | "careful_resolution"
    | "page_max_output_tokens"
    | "chat_levels"
    | "dynamic"
    | "scan_reader"
    | "mistral_model"
    | "daily_page_limit"
    | "ulc_ceiling_paise",
    number
  >;
}

/** POST /operator/scan-cost (#463): the scan settings and `dynamic` to price;
 * null is what a save would inherit (the ceiling's: the organisation's own, else the
 * platform's). customer_id null: the platform defaults. */
export type ScanCostAsk = { customer_id: string | null } & Pick<
  AiValues,
  | "scan_thinking"
  | "scan_resolution"
  | "careful_thinking"
  | "careful_resolution"
  | "page_max_output_tokens"
  | "dynamic"
  | "scan_reader"
  | "mistral_model"
  | "ulc_ceiling_paise"
>;

/** One kind of page read, as measured in the last `days`: usd_per_page null when no page
 * was read with these settings. */
export interface ScanCostRead {
  model: string;
  thinking: string;
  resolution: string;
  usd_per_page: number | null;
  pages: number;
}

/** The approximate cost a page: the first read, plus the share of pages read again (re_read
 * null when Dynamic is off) at their cost. usd_per_page null when the first read is not
 * measured; the first read's alone when the pages read again are not. quality: the share of
 * pages read cleanly (no word the AI was unsure of) at these settings and at max settings
 * (the same model, highest thinking, high picture detail). accuracy: the latest finished
 * check at these settings; always null for the platform defaults. */
export interface ScanCost {
  days: number;
  first_read: ScanCostRead;
  re_read: (ScanCostRead & { share: number | null }) | null;
  usd_per_page: number | null;
  quality: { clean_share: number | null; pages: number; max_clean_share: number | null; max_pages: number };
  // Tokens a page at these settings in the last `days`: null without data; limit_hits: pages
  // whose read reached the limit (cut off and read again in two halves).
  tokens: {
    input_per_page: number | null;
    output_per_page: number | null;
    output_p99: number | null;
    limit: number;
    limit_hits: number;
    pages: number;
  };
  accuracy: AccuracyCheck | null;
  // #473: Mistral charges a fixed price a page, not by tokens.
  fixed_price: boolean;
  fixed_usd_per_page: number | null;
  ulc?: UlcCost | null; // #488: only while ultra low cost reads
}

/** What ultra low cost measured in the last `days` (#488): the ceiling, the pages it read, their
 * cost a page and the dearest document's, and the pages read locally to stay within the ceiling. */
export interface UlcCost {
  ceiling_paise: number;
  ceiling_usd: number;
  pages: number;
  usd_per_page: number | null;
  top_usd_per_page: number | null;
  limit_hits: number;
}

/** One organisation's accuracy check (#463): recent pages read at its settings and at max
 * settings, and the share of words that match. No text is shown or kept. */
export interface AccuracyCheck {
  id: string;
  status: "running" | "done" | "failed";
  settings: { model: string } & Pick<AiValues, "scan_thinking" | "scan_resolution" | "careful_thinking" | "careful_resolution">;
  pages_checked: number;
  pages_failed: number;
  word_match: number | null;
  numbers_match: number | null; // amounts, dates and numbers; null: none on the checked pages
  max_pages: number; // the pages it reads
  cost_usd: number | null;
  started_at: string;
  finished_at: string | null;
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

/** #387: one organisation's numbers for the period (GET /operator/org-numbers?days=). */
export interface OrgNumbers {
  organisation_id: string;
  documents: number; // files uploaded in the period, every version, those in Trash too
  documents_in_trash: number;
  pages_read: number; // the pages of those files that were read, each page once
  pages_by_route: Record<string, number>; // how they were read: ai, local, text_layer, cells, blank, ocr, or a later route
  failed_documents: Record<string, number>; // failed, rejected or not readable yet, by reason code
  active_users: number; // people who did anything the activity log records
  questions: number; // replies to questions, a clarifying question back not counted
  ai_calls: number; // AI calls not served from the cache
  ai_input_tokens: number;
  ai_output_tokens: number;
  ai_cost_usd: number | null; // what OpenRouter reported; null: none known
  ai_cost_unknown_calls: number; // calls whose cost is not known or only estimated
}

export interface OrgNumbersReply {
  from: string;
  to: string;
  ai_enabled: boolean;
  organisations: OrgNumbers[];
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

/** PRD 6 F3: one organisation's replies in one ended ISO week (week: its Monday). Counts only;
 * null is a count the server hid (under 10, or what would give one away). */
export interface AnswerWeekRow {
  organisation_id: string;
  week: string;
  replies: number;
  answered: number | null;
  not_found: number | null;
  clarify: number | null;
  rated: number;
  useful: number | null;
}

/** GET /operator/answer-weeks?weeks= */
export interface AnswerWeeksReply {
  from: string;
  to: string;
  organisations: AnswerWeekRow[];
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
// "save": scanning only, the reader choice that reads clean printed pages on our server (#398).
export type TaskLevel = "none" | "save" | "low" | "high" | "max" | "off" | "on";

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

/** The three folder upload limits (#484): null means "not set here" (follow the platform, or
 * the platform's default). Most total size is in whole GB. */
export type FolderLimitName = "max_files" | "max_gb" | "max_folders";
export type FolderLimits = Record<FolderLimitName, number | null>;

/** GET /operator/folder-upload-limits: the platform's own values and the defaults below them. */
export interface PlatformFolderLimits extends FolderLimits {
  defaults: Record<FolderLimitName, number>;
}

/** GET /operator/orgs/{id}/folder-upload-limits: the organisation's own, the platform's, the defaults. */
export interface OrgFolderLimits {
  own: FolderLimits;
  platform: FolderLimits;
  defaults: Record<FolderLimitName, number>;
}

/** GET /operator/storage-limit and /operator/orgs/{id}/storage-limit (#516): how much an
 * organisation may store in all, in whole GB. storage_gb is this level's own (null: an
 * organisation follows the platform; the platform uses the default); effective_gb is the one that
 * applies; used_bytes is what the organisation has stored, Trash included (0 for the platform). */
export interface StorageLimit {
  storage_gb: number | null;
  platform_gb: number | null;
  effective_gb: number;
  default_gb: number;
  most_gb: number;
  used_bytes: number;
}

/** GET /admin/storage (#516): what this organisation has stored, Trash included, and its limit. */
export interface AdminStorage {
  used_bytes: number;
  limit_bytes: number;
  limit_gb: number;
}

/** GET /operator/speed (package S, 0053): each speed control's saved value (null: the server's
 * setting) and the most this server allows, set when it is deployed. */
export type SpeedName = "model_rpm" | "documents_at_once" | "pages_at_once";
export type Speed = Record<SpeedName, { value: number | null; most: number }>;

/** GET /operator/test-model: the whole platform on one model for a test, or not. */
export interface TestModel {
  active: boolean;
  scan_model: string | null;
  chat_model: string | null;
  started_at: string | null;
  since: { pages: number; questions: number; cost_usd: number | null; avg_seconds_per_page: number | null } | null; // null when none runs
  note: string | null;
}
