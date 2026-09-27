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

/** The largest file we send. The customer's own limit may be lower; the server decides. */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;

/** How often the list is asked again while a document is still being checked or read. */
export const POLL_MS = 3000;

export const PAGE_SIZE = 25;

function extension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

/**
 * Why a file will not be sent, in plain English, or null when it may be sent.
 * Checked before uploading so people get the answer at once.
 */
export function validateUpload(
  file: { name: string; size: number },
  maxBytes: number = MAX_UPLOAD_BYTES,
): string | null {
  const ext = extension(file.name);
  if (!(ACCEPTED_EXTENSIONS as readonly string[]).includes(ext)) {
    return "This kind of file can't be uploaded. Use PDF, PNG, JPG, TIFF, CSV or XLSX.";
  }
  if (file.size === 0) return "This file is empty.";
  if (file.size > maxBytes) {
    return `This file is larger than ${Math.floor(maxBytes / (1024 * 1024))} MB.`;
  }
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
