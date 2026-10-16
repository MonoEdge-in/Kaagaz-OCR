// The decisions behind the documents screen, kept out of the components so
// they are unit tested in node (tests/shell-documents.test.ts).

import { ApiError } from "@/lib/api";
import type { DocumentStatus, DocumentSummary, UploadLimits } from "@/lib/types";

/** File name endings the backend can take. The server checks the bytes again. */
export const ACCEPTED_EXTENSIONS = [
  "pdf",
  "png",
  "jpg",
  "jpeg",
  "tif",
  "tiff",
  "csv",
  "xlsx",
] as const;

/** For the file picker's accept attribute. */
export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.map((ext) => `.${ext}`).join(",");

type AcceptedExtension = (typeof ACCEPTED_EXTENSIONS)[number];

/** The size group a file falls in, the same names the server uses ("pdf", "image", "sheet"). */
export type UploadKind = "pdf" | "image" | "sheet";

const KIND_OF_EXTENSION: Readonly<Record<AcceptedExtension, UploadKind>> = {
  pdf: "pdf",
  png: "image",
  jpg: "image",
  jpeg: "image",
  tif: "image",
  tiff: "image",
  csv: "sheet",
  xlsx: "sheet",
};

const MB = 1024 * 1024;

/**
 * The platform's limits (PRD U2; MAX_PAGES in .env.example), used only until
 * GET /upload-limits answers, or when it fails. The server's own answer
 * already has the customer's lower limit in it, and it checks every file again.
 */
export const FALLBACK_UPLOAD_LIMITS: Readonly<UploadLimits> = {
  pdf_bytes: 50 * MB,
  image_bytes: 20 * MB,
  sheet_bytes: 10 * MB,
  max_pages: 100,
  readers: [], // no "Scan with" until the server says which readers it has
};

/** The limits in words, shown next to the dropzone. */
export function uploadLimitsHint(limits: UploadLimits = FALLBACK_UPLOAD_LIMITS): string {
  return (
    `PDF up to ${Math.floor(limits.pdf_bytes / MB)} MB. ` +
    `PNG, JPG or TIFF up to ${Math.floor(limits.image_bytes / MB)} MB. ` +
    `CSV or XLSX up to ${Math.floor(limits.sheet_bytes / MB)} MB.`
  );
}

export const PAGE_SIZE = 25;

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

function isAccepted(ext: string): ext is AcceptedExtension {
  return (ACCEPTED_EXTENSIONS as readonly string[]).includes(ext);
}

/** The kind a file name's ending stands for, in any letter case, or null when it is not accepted. */
export function uploadKind(name: string): UploadKind | null {
  const ext = extension(name);
  return isAccepted(ext) ? KIND_OF_EXTENSION[ext] : null;
}

/** The largest file of this kind that may be sent. */
export function limitFor(kind: UploadKind, limits: UploadLimits = FALLBACK_UPLOAD_LIMITS): number {
  switch (kind) {
    case "pdf":
      return limits.pdf_bytes;
    case "image":
      return limits.image_bytes;
    case "sheet":
      return limits.sheet_bytes;
  }
}

/**
 * The refusal for a file over its limit, naming the limit, the size and the
 * fix. The same words as the server's too_large_message. The size is rounded
 * up, so a file just over 20 MB says 21 MB, never 20; the limit is rounded
 * down, so it is never overstated.
 */
export function tooLargeMessage(kind: UploadKind, sizeBytes: number, limitBytes: number): string {
  const limit = Math.floor(limitBytes / MB);
  const size = Math.ceil(sizeBytes / MB);
  switch (kind) {
    case "pdf":
      return `PDFs can be up to ${limit} MB. This one is ${size} MB. Split it or save it smaller and try again.`;
    case "image":
      return `Photos and scans can be up to ${limit} MB. This one is ${size} MB. Save it at a lower resolution and try again.`;
    case "sheet":
      return `Spreadsheets and CSV files can be up to ${limit} MB. This one is ${size} MB. Split it into smaller files and try again.`;
  }
}

/**
 * Why a file will not be sent, in plain language, or null when it may be sent.
 * Checked before uploading so people get the answer at once. The limit is the
 * one for the file's kind, from the server's limits once they are known.
 */
export function validateUpload(
  file: { name: string; size: number },
  limits: UploadLimits = FALLBACK_UPLOAD_LIMITS,
): string | null {
  const kind = uploadKind(file.name);
  if (kind === null) {
    return "This kind of file can't be uploaded. Use PDF, PNG, JPG, TIFF, CSV or XLSX.";
  }
  if (file.size === 0) return "This file is empty.";
  const limit = limitFor(kind, limits);
  if (file.size > limit) return tooLargeMessage(kind, file.size, limit);
  return null;
}

const SETTLING: readonly DocumentStatus[] = ["received", "clean"];

interface Polled {
  status: DocumentStatus;
  newer_version?: { status: DocumentStatus } | null;
}

/**
 * Keep asking the server only while something is still being checked or read,
 * a newer version included, so the status_message the server writes as it
 * goes ("Reading page 3 of 40…", PRD 2 X3) keeps moving.
 */
export function shouldPoll(items: readonly Polled[]): boolean {
  return items.some((item) => {
    const newer = item.newer_version?.status;
    return SETTLING.includes(item.status) || (newer !== undefined && SETTLING.includes(newer));
  });
}

export interface PageInfo {
  from: number; // 1-based first row shown, 0 when there are none
  to: number;
  hasPrevious: boolean;
  hasNext: boolean;
}

export function pageInfo(offset: number, shown: number, total: number): PageInfo {
  return {
    from: shown === 0 ? 0 : offset + 1,
    to: offset + shown,
    hasPrevious: offset > 0,
    hasNext: offset + shown < total,
  };
}

/** After deleting the last row of a later page, step back one page. */
export function offsetAfterDelete(offset: number, shownBefore: number, pageSize: number): number {
  return shownBefore <= 1 && offset > 0 ? Math.max(0, offset - pageSize) : offset;
}

/** A PDF is estimated at this many MB a page (PRD 2, X4). */
export const MB_PER_PDF_PAGE = 0.4;

/**
 * Seconds to read one page, by file type (PRD 2, X4), measured on 30 Sep 2026
 * with `python -m kaagaz.cli measure --file <path>` on the build laptop, with
 * OCR_ENGINE=rapidocr and made-up files (docs/architecture.md, Decisions):
 * a scanned page took 5.4 to 10.1 s (6.9 on average), a photo 6.6 s, a text-layer
 * PDF page 0.05 s, and a 200-row sheet 0.01 s. A PDF can't be told apart before
 * it is read, so it is estimated as a scan: the text "Up to N minutes" is then
 * true for both. With the model on, the card and invoice calls add time these
 * numbers don't include. Changed (council 16 Oct 2026): a PDF or photo page is now
 * 35 s, as Gemini reads scans at High thinking (30 to 130 s a page), so the upload
 * row's last byte is not most of its bar.
 */
export const SECONDS_PER_PAGE: Readonly<Record<UploadKind, number>> = {
  pdf: 35,
  image: 35,
  sheet: 1,
};

/**
 * Pages to expect, rounded up and never below 1. A photo or a sheet is 1.
 * For a PDF, `counted` is the page count the upload flow found before sending
 * (pageCount.ts), or null when it could not count them. PROVISIONAL: the PRD
 * (X4) says MB ÷ MB_PER_PDF_PAGE, but the real count is more accurate, so it
 * wins; MB ÷ MB_PER_PDF_PAGE is used only when the count is not known.
 */
export function estimatePages(kind: UploadKind, sizeBytes: number, counted: number | null = null): number {
  if (kind !== "pdf") return 1;
  if (counted !== null && Number.isFinite(counted)) return Math.max(1, Math.ceil(counted));
  if (!(sizeBytes > 0)) return 1;
  return Math.max(1, Math.ceil(sizeBytes / (MB_PER_PDF_PAGE * MB)));
}

/**
 * Rough but useful estimate for how long ingest will take after the file is
 * received. `counted`: a PDF's real page count when known (estimatePages).
 */
export function estimateIngestSeconds(
  kind: UploadKind,
  sizeBytes: number,
  filesCount = 1,
  counted: number | null = null,
): number {
  const estimate =
    estimatePages(kind, sizeBytes, counted) * SECONDS_PER_PAGE[kind] +
    Math.max(filesCount - 1, 0) * 12;
  return Math.max(4, Math.round(estimate));
}

/** The estimate in words: "Up to N minutes" for a PDF (rounded up, at least 1), else formatEtaText. */
export function estimateText(kind: UploadKind, seconds: number): string {
  if (kind !== "pdf") return formatEtaText(seconds);
  const minutes = Math.max(1, Math.ceil(seconds / 60));
  return `Up to ${minutes} minute${minutes === 1 ? "" : "s"}`;
}

export function formatEtaText(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `About ${total} second${total === 1 ? "" : "s"}`;

  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  if (remainder === 0) {
    return `About ${minutes} minute${minutes === 1 ? "" : "s"}`;
  }
  return `About ${minutes} minute${minutes === 1 ? "" : "s"} ${remainder} second${remainder === 1 ? "" : "s"}`;
}

export function formatProgressPercent(done: number, total: number): string {
  if (total <= 0) return "0%";
  return `${Math.round((done / total) * 100)}%`;
}

// PRD 5 U10: sending (bytes sent), saving (from the last byte to the server's reply),
// reading (the stored document, followed through GET /api/batches/{id} until it is
// no longer being read), then done, with the document when it was followed.
export type UploadState =
  | { kind: "waiting" }
  | { kind: "sending"; fraction: number }
  | { kind: "saving" }
  | { kind: "reading"; documentId: string; document: DocumentSummary }
  | { kind: "done"; documentId: string; document?: DocumentSummary }
  // inTrash: the earlier copy is in Trash, so the row offers Restore.
  | { kind: "duplicate"; documentId: string; existingName: string; inTrash: boolean; restoreError?: string }
  | { kind: "restoring"; documentId: string; existingName: string }
  | { kind: "restored"; documentId: string; existingName: string }
  // retry: it was not sent (the connection or the server), and this tab still has the file.
  | { kind: "error"; message: string; retry?: boolean };

/**
 * One line of words for an upload's state, read out by screen readers. The
 * percentage is left out on purpose, so the live region does not chatter; the
 * progress bar carries it.
 */
export function uploadStateText(state: UploadState): string {
  switch (state.kind) {
    case "waiting":
      return "Waiting to upload";
    // Changed on purpose (scan states, 16 Oct 2026): the brief's words, Uploading then Scanning.
    case "sending":
      return "Uploading";
    case "saving":
      return "Saving";
    case "reading":
      return "Scanning";
    case "done": {
      const { document } = state;
      if (!document) return "Uploaded";
      if (document.status !== "ready") return document.failure_code === "customer_disabled" ? "Stopped" : "Not read";
      return document.first_unreadable ? `Page ${document.first_unreadable.page_no} to check` : "Ready";
    }
    case "duplicate":
      return state.inTrash ? "Already uploaded, in Trash" : "Already uploaded";
    case "restoring":
      return "Restoring";
    case "restored":
      return "Restored from Trash";
    case "error":
      return state.retry ? "Not sent" : "Not uploaded";
  }
}

/**
 * The server's words when another account has signed in on this browser
 * since the file was chosen (409 session_changed, critical #5); null for any
 * other failure. Once said, no file still waiting in this tab is sent.
 */
export function sessionChangedMessage(caught: unknown): string | null {
  return caught instanceof ApiError && caught.code === "session_changed" ? caught.message : null;
}

/** The words for an upload that failed. Offline, the file must be chosen again: nothing keeps it. */
export function uploadErrorText(caught: unknown, online: boolean): string {
  if (!online) {
    return "Not uploaded: no internet connection. Choose the file again when you're back online.";
  }
  return caught instanceof ApiError ? caught.message : "Upload failed. Check your connection, then try again.";
}

/**
 * Said once, politely, when a batch of uploads settles, in the rows' own
 * words. A search or filter may hide the new documents (hidden).
 */
export function uploadsFinishedText(
  counts: { uploaded: number; already: number; failed: number },
  hidden: boolean,
): string {
  const parts = [
    counts.uploaded > 0 ? `${counts.uploaded} uploaded` : "",
    counts.already > 0 ? `${counts.already} already uploaded` : "",
    counts.failed > 0 ? `${counts.failed} not uploaded` : "",
  ].filter(Boolean);
  const hint = hidden && counts.uploaded + counts.already > 0 ? " Clear search or filters to see them." : "";
  return `Uploads finished: ${parts.join(", ")}.${hint}`;
}

/**
 * What the list area shows. A failed refresh keeps the list it already has.
 * Nothing left by a search or by filters is "no-match", never "empty".
 */
export type ListView = "loading" | "error" | "empty" | "no-match" | "list";

export function listView(
  total: number | null,
  failed: boolean,
  q: string,
  filtered = false,
): ListView {
  if (total === null) return failed ? "error" : "loading";
  if (total === 0) return q === "" && !filtered ? "empty" : "no-match";
  return "list";
}

/** The polite announcement once a document is gone from the list: Delete moves it to Trash. */
export function deletedMessage(name: string): string {
  return `Moved ${name} to Trash.`;
}

// --- Scan states (16 Oct 2026, scan-review UX rules section 3 and 6) ----------------------

type Scanned = Pick<DocumentSummary, "progress" | "attempts" | "max_attempts">;

export const counted = (n: number, one: string) => `${n} ${n === 1 ? one : `${one}s`}`;

/**
 * A document being checked or read, as a status chip word and the words after it, read
 * together: "Scanning" "page 6 of 14". From its progress, so the words never depend on
 * the server's status_message; null with no progress (queued: the status_message says
 * what it waits for, "Waiting to start." and the like).
 */
export function scanWords(document: Scanned): { chip: string; detail: string } | null {
  const { progress } = document;
  if (!progress) return null;
  const { step, done, total } = progress;
  const pageOf = total > 0 ? `page ${Math.max(1, Math.min(done + 1, total))} of ${total}` : "the whole file";
  let words: { chip: string; detail: string };
  if (step === "scanning") words = { chip: "Checking", detail: "for viruses" };
  else if (step === "preparing") words = { chip: "Preparing", detail: pageOf };
  else if (step === "rereading") words = { chip: "Scanning", detail: `${counted(total, "page")} again, carefully` };
  else if (step === "understanding") words = { chip: "Understanding", detail: "the document" };
  else if (step === "indexing") words = { chip: "Indexing", detail: "for search" };
  else words = { chip: "Scanning", detail: pageOf };
  const { attempts, max_attempts: most } = document;
  if (attempts >= 2 && most) words.detail += ` · Try ${Math.min(attempts, most)} of ${most}`;
  return words;
}

/** The words as one line, "Scanning page 6 of 14"; the server's words while it is queued. */
export function scanPhrase(document: Scanned & Pick<DocumentSummary, "status_message">): string {
  const words = scanWords(document);
  return words ? `${words.chip} ${words.detail}` : document.status_message;
}

type Finished = Pick<DocumentSummary, "pages_unreadable" | "pages_uncertain" | "words_unsure" | "values_to_review">;

/**
 * Flagged for review: the unsure words plus the values labelled needs review. Pages to
 * check are counted on their own, as attentionWords (library.ts) counts them.
 */
export function flaggedCount(document: Finished): number {
  return (document.words_unsure ?? 0) + document.values_to_review;
}

export function pagesToCheck(document: Finished): number {
  return document.pages_unreadable + ((document.words_unsure ?? 0) === 0 ? document.pages_uncertain : 0);
}

/** Something to look at once it is read: the row then ends with "Open to review". */
export const toReview = (document: Finished) => flaggedCount(document) > 0 || pagesToCheck(document) > 0;

/** After "Scan complete": "12 flagged for review · 2 pages to check", or "Nothing flagged". */
export function flaggedText(document: Finished): string {
  return flaggedWords(flaggedCount(document), pagesToCheck(document));
}

/** flaggedText for counts, summed over several documents (the status bar). */
export function flaggedWords(flagged: number, pages: number): string {
  const parts = [flagged > 0 ? `${flagged} flagged for review` : "", pages > 0 ? `${counted(pages, "page")} to check` : ""];
  return parts.filter(Boolean).join(" · ") || "Nothing flagged";
}
