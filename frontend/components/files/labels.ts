// The four sensitivity labels (File management O2): their names and short descriptions, in
// one place so the wording is easy to change. A label informs; it restricts nothing.
// Pure data and functions, unit tested in node (tests/file-management.test.ts).

import type { Sensitivity } from "@/lib/types";

/** Weakest first: the order of the pickers, the filter and the Label sort. */
export const LABELS: readonly Sensitivity[] = ["public", "internal", "confidential", "restricted"];

export const LABEL_NAMES: Record<Sensitivity, string> = {
  public: "Public",
  internal: "Internal",
  confidential: "Confidential",
  restricted: "Restricted",
};

export const LABEL_DESCRIPTIONS: Record<Sensitivity, string> = {
  public: "Fine to share outside the company.",
  internal: "For people in the company. Most documents start here.",
  confidential:
    "A label for sensitive papers. Land records, contracts and bank statements start here. Everyone in your organisation can still open it.",
  restricted:
    "A label for papers to handle with care, such as deeds and personal papers. Everyone in your organisation can still open it.",
};

/** "Confidential"; a label the screen does not know (a newer server) is shown as sent. */
export function labelName(label: string): string {
  return Object.hasOwn(LABEL_NAMES, label) ? LABEL_NAMES[label as Sensitivity] : label;
}

export function isLabel(value: unknown): value is Sensitivity {
  return typeof value === "string" && (LABELS as readonly string[]).includes(value);
}
