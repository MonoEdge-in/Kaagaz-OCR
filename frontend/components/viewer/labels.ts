// The words shown beside every label, so colour is never the only signal. One
// place, so the documents page, the answer cards and the review queue say the
// same thing.

import type { Label, LabelReason, ValueVotes } from "@/lib/types";

export function labelText(label: Label): string {
  return label === "verified" ? "Verified" : "Needs review";
}

export function labelBadgeClass(label: Label): string {
  return label === "verified" ? "badge badge-verified" : "badge badge-review";
}

/** The icon beside each label: a check seal for Verified, an eye for Needs review. */
export function labelIcon(label: Label): "check-seal" | "eye-alert" {
  return label === "verified" ? "check-seal" : "eye-alert";
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

/** The review queue's short names for PRD 5's reasons (U6), as its reason filter lists them. */
export const REASON_FILTER_TEXT = {
  unsure_text: "Hard to read",
  box_unanchored: "Spot not confirmed",
  local_reader: "Read by the backup reader",
  ocr_script: "Read from Hindi or Marathi script",
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
