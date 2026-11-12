// The words shown beside every label, so colour is never the only signal. One
// place, so the documents page, the answer cards and the review queue say the
// same thing.

import type { Label, LabelReason, ValueVotes } from "@/lib/types";

export function labelText(label: Label): string {
  return label === "verified" ? "Verified" : "Ready to verify";
}

/**
 * A label's tooltip (Tushar, 16 Oct 2026): "Confidence 72%" from the reader's 0 to 1 score,
 * rounded; "Confidence not measured" when no score was measured. Never shown by default.
 */
export function confidenceTip(score: number | null | undefined): string {
  return typeof score === "number" && Number.isFinite(score)
    ? `Confidence ${Math.round(Math.min(1, Math.max(0, score)) * 100)}%`
    : "Confidence not measured";
}

export function labelBadgeClass(label: Label): string {
  return label === "verified" ? "badge badge-verified" : "badge badge-review";
}

/** The icon on a label's pill (the Attention count): a check seal for Verified, an eye for Ready to verify. */
export function labelIcon(label: Label): "check-seal" | "eye-alert" {
  return label === "verified" ? "check-seal" : "eye-alert";
}

/**
 * A value's quiet mark (owner, 29 Oct 2026: a pill on every line looked unsure): a check seal
 * for Verified, a small dot for Ready to verify (#453: the dashed ring looked unsure too).
 * Different shapes, so colour is never the only sign.
 */
export function labelMarkIcon(label: Label): "check-seal" | "dot" {
  return label === "verified" ? "check-seal" : "dot";
}

/** The mark's tooltip: its label in plain words, then why, when the value's reason is known. */
export function labelTip(label: Label, reason?: LabelReason): string {
  const said = label === "verified" ? "Verified" : "Ready to verify: not checked yet";
  return reason ? `${said}. ${reasonText(reason)}` : `${said}.`;
}

const REASONS: Record<LabelReason, string> = {
  direct_cell: "Read straight from a spreadsheet cell.",
  readers_agree: "Two separate readers read the same value.",
  readers_disagree: "The readers read different values.",
  single_reader: "Only one reader could read this value.",
  low_confidence: "The reader was not sure of this value.",
  corrected: "Corrected by a reviewer.",
  learned_correction: "Fixed by a correction a reviewer asked to remember.",
  model_chosen: "Found by the AI. Check it against the page.",
  ocr_script: "Read from a scan in Hindi, Marathi or mixed script. Check it against the page.",
  check_failed: "A check on this invoice failed. Check the numbers.",
  unsure_text: "Part of this value was hard to read. Check it against the page.",
  box_unanchored: "Check the exact spot of this value against the page.",
  local_reader: "Read by the backup reader while the main reader was down. It will be read again automatically.",
  mixed_digits: "This amount has digits of two scripts. Check each digit against the page.",
  amount_commas: "The commas in this amount fit no usual grouping. Check each digit against the page.",
  amount_mark: "A mark in this amount may be misread, such as a /- read as 1. Check each digit against the page.",
  amount_outlier: "This total is more than ten times the other total on its page. Check each digit against the page.",
};

export function reasonText(reason: LabelReason): string {
  return REASONS[reason];
}

/**
 * Ensemble's vote on a value, in plain words (feature 27, Tushar 15 Oct 2026): "2 of 3
 * readers read 27AAKFS8812L1Z9." (and what the main reader read when it was outvoted), or,
 * with no majority, what each read. Never a model's name.
 */
export function voteText(votes: ValueVotes): string {
  const main = votes.reads.find((read) => read.main);
  if (votes.value !== null) {
    const said = `${votes.agreed} of ${votes.readers} readers read ${votes.value}.`;
    return main?.text && main.text !== votes.value ? `${said} The main reader read ${main.text}.` : said;
  }
  const reads = votes.reads.map((read) => {
    const who = read.main ? "the main reader" : "another reader";
    return read.text === null ? `${who} found nothing here` : `${who} read ${read.text}`;
  });
  return `The readers did not agree: ${reads.join("; ")}. Check it against the page.`;
}

/** The review queue's short names for PRD 5's reasons (U6) and #524's amount reasons, as its
 * reason filter lists them. */
export const REASON_FILTER_TEXT = {
  unsure_text: "Hard to read",
  box_unanchored: "Spot not confirmed",
  local_reader: "Read by the backup reader",
  ocr_script: "Read from Hindi or Marathi script",
  mixed_digits: "Digits of two scripts in one amount",
  amount_commas: "The amount's commas look wrong",
  amount_mark: "A mark in the amount looks wrong",
  amount_outlier: "Much bigger than the other total",
} as const satisfies Partial<Record<LabelReason, string>>;

/** Tax and bank short forms, kept in capitals the way people write them. */
const UPPER = new Set(["gstin", "gst", "pan", "hsn", "sac", "ifsc", "tds", "cgst", "sgst", "igst"]);

/** "invoice_number" -> "Invoice number", "seller_gstin" -> "Seller GSTIN". */
export function fieldText(field: string): string {
  const words = field
    .split("_")
    .map((word) => (UPPER.has(word.toLowerCase()) ? word.toUpperCase() : word))
    .join(" ")
    .trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : field;
}
