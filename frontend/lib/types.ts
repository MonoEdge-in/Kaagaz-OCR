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
  | "check_failed"; // PRD 2, K3: a check on its invoice failed, even if the readers agreed

/** "admin" only for a session started at /admin/login (PRD A1). */
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
    totp_enabled: boolean; // two-step sign-in is on (each admin chooses)
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
  doc_type: DocType | null; // from the AI model's document card; null without one (PRD 2, D6)
  languages: string[]; // "en", "hi", "mr", "other"; [] when not known
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
  step: "scanning" | "preparing" | "reading" | "understanding" | "indexing";
  done: number;
  total: number; // 0 for a sheet, which is read as a whole
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

export interface PageSummary {
  page_no: number;
  width: number;
  height: number;
  unit: "pt" | "px" | "cells";
  has_image: boolean;
  route: PageRoute | null; // null: not read (yet)
  effective_dpi: number | null; // of the largest image on the page; null when it has none
  low_resolution: boolean; // effective_dpi under LOW_RES_DPI; PRD 3 shows the warning
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
  page: PageSummary | null; // the cited page, for the preview; null once in Trash or deleted
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
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  created_at: string;
  answer: Answer | null; // set for assistant messages only
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
}
