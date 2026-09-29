// The decisions behind the documents screen, kept out of the components so
// they are unit tested in node (tests/shell-documents.test.ts).

import type { DocumentStatus, DocumentSummary, Me } from "@/lib/types";

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

/** The largest file of each kind (PRD U2). The server checks again after reading the first bytes. */
export const UPLOAD_LIMIT_BYTES: Readonly<Record<UploadKind, number>> = {
  pdf: 50 * MB,
  image: 20 * MB,
  sheet: 10 * MB,
};

/** The largest file of any kind. The customer's own limit may be lower; the server decides. */
export const MAX_UPLOAD_BYTES = Math.max(...Object.values(UPLOAD_LIMIT_BYTES));

/** The limits in words, shown next to the dropzone. */
export const UPLOAD_LIMITS_HINT =
  `PDF up to ${UPLOAD_LIMIT_BYTES.pdf / MB} MB. ` +
  `PNG, JPG or TIFF up to ${UPLOAD_LIMIT_BYTES.image / MB} MB. ` +
  `CSV or XLSX up to ${UPLOAD_LIMIT_BYTES.sheet / MB} MB.`;

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

/**
 * The largest file of this kind that may be sent. A customer's own limit can
 * only lower it, never raise it; null means the customer has none.
 */
export function limitFor(kind: UploadKind, customerMaxBytes: number | null = null): number {
  const typeLimit = UPLOAD_LIMIT_BYTES[kind];
  return customerMaxBytes === null ? typeLimit : Math.min(typeLimit, customerMaxBytes);
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
 * one for the file's kind, lowered by the customer's own limit when one is known.
 */
export function validateUpload(
  file: { name: string; size: number },
  customerMaxBytes: number | null = null,
): string | null {
  const kind = uploadKind(file.name);
  if (kind === null) {
    return "This kind of file can't be uploaded. Use PDF, PNG, JPG, TIFF, CSV or XLSX.";
  }
  if (file.size === 0) return "This file is empty.";
  const limit = limitFor(kind, customerMaxBytes);
  if (file.size > limit) return tooLargeMessage(kind, file.size, limit);
  return null;
}

const SETTLING: readonly DocumentStatus[] = ["received", "clean"];

/** Keep asking the server only while something is still being checked or read. */
export function shouldPoll(items: readonly Pick<DocumentSummary, "status">[]): boolean {
  return items.some((item) => SETTLING.includes(item.status));
}

/**
 * Whether to show Delete. The uploader is matched by display name, which is a
 * hint for the screen only: the server checks the real uploader and the role.
 */
export function canDelete(me: Me, document: Pick<DocumentSummary, "uploaded_by_name">): boolean {
  if (me.user.role === "admin") return true;
  return document.uploaded_by_name !== null && document.uploaded_by_name === me.user.display_name;
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

/** Rough but useful estimate for how long ingest will take after the file is received. */
export function estimateIngestSeconds(sizeBytes: number, filesCount = 1): number {
  const megabytes = Math.max(sizeBytes / (1024 * 1024), 0.1);
  const estimate = megabytes * 8 + Math.max(filesCount - 1, 0) * 12;
  return Math.max(4, Math.round(estimate));
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
  | { kind: "duplicate"; documentId: string; existingName: string }
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
      return "Already uploaded";
    case "error":
      return "Not uploaded";
  }
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

/** The polite announcement once a document is gone from the list. */
export function deletedMessage(name: string): string {
  return `Deleted ${name}.`;
}
