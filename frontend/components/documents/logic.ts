// The decisions behind the documents screen, kept out of the components so
// they are unit tested in node (tests/shell-documents.test.ts).

import type { DocumentStatus, UploadLimits } from "@/lib/types";

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
};

/** The limits in words, shown next to the dropzone. */
export function uploadLimitsHint(limits: UploadLimits = FALLBACK_UPLOAD_LIMITS): string {
  return (
    `PDF up to ${Math.floor(limits.pdf_bytes / MB)} MB. ` +
    `PNG, JPG or TIFF up to ${Math.floor(limits.image_bytes / MB)} MB. ` +
    `CSV or XLSX up to ${Math.floor(limits.sheet_bytes / MB)} MB.`
  );
}

/** How often the list is asked again while a document is still being checked or read. */
export const POLL_MS = 3000;

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
 * Seconds to read one page, by file type (PRD 2, X4). PLACEHOLDER numbers:
 * `python -m kaagaz.cli measure --file <path>` on the reference laptop sets
 * the measured ones, which go here and in docs/architecture.md (Decisions).
 * Until it has run with a real OCR (NOT RUN), every type keeps today's rate
 * of 8 seconds per MB, at MB_PER_PDF_PAGE MB a page: 8 × 0.4 = 3.2 seconds.
 */
export const SECONDS_PER_PAGE: Readonly<Record<UploadKind, number>> = {
  pdf: 3.2,
  image: 3.2,
  sheet: 3.2,
};

/** Pages to expect: a PDF's MB ÷ MB_PER_PDF_PAGE, rounded up; a photo or a sheet is 1. Never below 1. */
export function estimatePages(kind: UploadKind, sizeBytes: number): number {
  if (kind !== "pdf" || !(sizeBytes > 0)) return 1;
  return Math.max(1, Math.ceil(sizeBytes / (MB_PER_PDF_PAGE * MB)));
}

/** Rough but useful estimate for how long ingest will take after the file is received. */
export function estimateIngestSeconds(kind: UploadKind, sizeBytes: number, filesCount = 1): number {
  const estimate =
    estimatePages(kind, sizeBytes) * SECONDS_PER_PAGE[kind] + Math.max(filesCount - 1, 0) * 12;
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

export type UploadState =
  | { kind: "waiting" }
  | { kind: "uploading"; fraction: number }
  | { kind: "done"; documentId: string }
  // inTrash: the earlier copy is in Trash, so the row offers Restore.
  | { kind: "duplicate"; documentId: string; existingName: string; inTrash: boolean; restoreError?: string }
  | { kind: "restoring"; documentId: string; existingName: string }
  | { kind: "restored"; documentId: string; existingName: string }
  | { kind: "error"; message: string };

/**
 * One line of words for an upload's state, read out by screen readers. The
 * percentage is left out on purpose, so the live region does not chatter; the
 * progress bar carries it.
 */
export function uploadStateText(state: UploadState): string {
  switch (state.kind) {
    case "waiting":
      return "Waiting to upload";
    case "uploading":
      return "Uploading";
    case "done":
      return "Uploaded";
    case "duplicate":
      return state.inTrash ? "Already uploaded, in Trash" : "Already uploaded";
    case "restoring":
      return "Restoring";
    case "restored":
      return "Restored from Trash";
    case "error":
      return "Not uploaded";
  }
}

/**
 * Whether the screen reloads once a batch of uploads settles: nothing is still
 * going, something new came of it, and no duplicate in Trash is waiting for
 * the person to press Restore (a reload would take that choice away).
 */
export function shouldReloadAfterUploads(states: readonly UploadState[]): boolean {
  const waiting = states.some(
    (state) =>
      state.kind === "waiting" ||
      state.kind === "uploading" ||
      state.kind === "restoring" ||
      (state.kind === "duplicate" && state.inTrash),
  );
  const changed = states.some(
    (state) => state.kind === "done" || state.kind === "duplicate" || state.kind === "restored",
  );
  return !waiting && changed;
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
