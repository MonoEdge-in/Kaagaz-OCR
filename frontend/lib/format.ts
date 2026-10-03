// Small display helpers shared by every screen: sizes, dates and times for India,
// and the words and tone for each document status. Pure functions, so
// they are unit tested in node (tests/format.test.ts).

import type { DetectedType, DocType, DocumentStatus } from "./types";

// The product is for Indian businesses, so times are shown in India time
// whatever the browser's own zone is. This also keeps the tests stable.
const TIME_ZONE = "Asia/Kolkata";

const dateTimeFormat = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
  timeZone: TIME_ZONE,
});

const dateFormat = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: TIME_ZONE,
});

const relativeFormat = new Intl.RelativeTimeFormat("en-IN", { numeric: "auto" });
const numberFormat = new Intl.NumberFormat("en-IN");

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** 1536 -> "1.5 KB". Base 1024, one decimal above bytes, trailing ".0" dropped. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const text = unit === 0 ? String(value) : value.toFixed(1).replace(/\.0$/, "");
  return `${text} ${BYTE_UNITS[unit]}`;
}

function parse(iso: string): Date | null {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** "27 Sept 2026, 2:30 pm" in India time. An unreadable date gives "". */
export function formatDateTime(iso: string): string {
  const date = parse(iso);
  return date ? dateTimeFormat.format(date) : "";
}

/** "27 Sept 2026" in India time. */
export function formatDate(iso: string): string {
  const date = parse(iso);
  return date ? dateFormat.format(date) : "";
}

/**
 * "now", "5 minutes ago", "yesterday", up to a week; older gives the date.
 * `now` is passed in (milliseconds), so rendering stays pure and tests are exact.
 */
export function formatRelative(iso: string, now: number): string {
  const date = parse(iso);
  if (!date) return "";
  const seconds = Math.round((date.getTime() - now) / 1000);
  const past = -seconds;
  if (past < 45) return relativeFormat.format(0, "second");
  if (past < 60 * 60) return relativeFormat.format(-Math.round(past / 60), "minute");
  if (past < 24 * 60 * 60) return relativeFormat.format(-Math.round(past / 3600), "hour");
  if (past < 7 * 24 * 60 * 60) return relativeFormat.format(-Math.round(past / 86400), "day");
  return formatDate(iso);
}

/** Indian digit grouping: 120000 -> "1,20,000". */
export function formatNumber(value: number): string {
  return numberFormat.format(value);
}

const TYPE_LABELS: Record<DetectedType, string> = {
  pdf: "PDF",
  png: "Image (PNG)",
  jpeg: "Image (JPEG)",
  tiff: "Image (TIFF)",
  zip: "Spreadsheet",
  ole2: "Old Office file",
  text: "CSV",
};

/** The file kind in words, from the type the server detected from the bytes. */
export function typeLabel(type: DetectedType): string {
  return TYPE_LABELS[type];
}

const DOC_TYPE_LABELS: Record<DocType, string> = {
  invoice: "Invoice",
  receipt: "Receipt",
  bank_statement: "Bank statement",
  land_record: "Land record",
  contract: "Contract",
  register: "Register",
  form: "Form",
  letter: "Letter",
  other: "Other",
};

/** A doc_type the screens know; anything else from the server is shown as "Unknown". */
export function isDocType(value: unknown): value is DocType {
  return typeof value === "string" && Object.hasOwn(DOC_TYPE_LABELS, value);
}

/** What kind of document it is, in words; "Unknown" without a type, as with the model off (PRD 2, D6). */
export function docTypeLabel(type: DocType | null): string {
  return type === null ? "Unknown" : DOC_TYPE_LABELS[type];
}

/**
 * Document status tones for `.status-chip` in globals.css. These are not the
 * Verified / Needs review label styles: those belong to values and answers only.
 */
export type StatusTone = "ready" | "working" | "neutral" | "danger";

export interface StatusChip {
  text: string; // always shown, so colour is never the only signal
  tone: StatusTone; // rendered as data-tone
  className: "status-chip";
}

const STATUS_CHIPS: Record<DocumentStatus, { text: string; tone: StatusTone }> = {
  received: { text: "Checking", tone: "working" },
  clean: { text: "Reading", tone: "working" },
  ready: { text: "Ready", tone: "ready" },
  needs_reader: { text: "Stored", tone: "neutral" },
  rejected: { text: "Rejected", tone: "danger" },
  failed: { text: "Failed", tone: "danger" },
};

/** The words, tone and class for a document status chip. */
export function statusChip(status: DocumentStatus): StatusChip {
  return { ...STATUS_CHIPS[status], className: "status-chip" };
}
