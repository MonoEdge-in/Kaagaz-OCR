// The words shown beside every label, so colour is never the only signal. One
// place, so the documents page, the answer cards and the review queue say the
// same thing.

import type { Label, LabelReason } from "@/lib/types";

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
  readers_disagree: "The two readers read different values.",
  single_reader: "Only one reader could read this value.",
  low_confidence: "The reader was not sure of this value.",
  corrected: "Corrected by a reviewer.",
  learned_correction: "Fixed by a correction a reviewer asked to remember.",
  model_chosen: "Found by the AI model. Check it against the page.",
  ocr_script: "Read by OCR from Hindi, Marathi or mixed-script text. Check it against the page.",
  check_failed: "A check on this invoice failed. Check the numbers.",
  unsure_text: "Part of this value was hard to read. Check it against the page.",
  box_unanchored: "The exact spot of this value on the page couldn't be confirmed. Check it against the page.",
  local_reader: "Read by the backup reader while the main reader was down. It will be read again automatically.",
};

export function reasonText(reason: LabelReason): string {
  return REASONS[reason];
}

/** "invoice_number" -> "Invoice number". */
export function fieldText(field: string): string {
  const words = field.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : field;
}
