// The words on File management's filter chips (Q5): each chip says what it is set to, so the
// row alone tells what the list or the search shows. Pure functions, unit tested in node
// (tests/file-management.test.ts).

import { formatDate } from "@/lib/format";
import type { Sensitivity } from "@/lib/types";
import { labelName } from "./labels";

export const FILTER_TEXT = {
  department: "Department",
  owner: "Owner",
  label: "Label",
  party: "Party",
  property: "Property",
  documentDate: "Document date",
  newer: "Has newer version",
  anyDepartment: "Any department",
  anyone: "Anyone",
  partyLegend: "Show documents naming this party",
  partyHint: "A name on the document, such as Shinde",
  propertyLegend: "Show documents for this property",
  propertyHint: "A survey or gat number, such as 44 or 44/2",
  labelLegend: "Show documents with any of these labels",
  departmentLegend: "Show documents in this department",
  ownerLegend: "Show documents owned by",
  documentDateLegend: "Show documents dated",
  apply: "Apply",
  loadingPeople: "Loading people…",
  noDepartments: "No departments yet.",
};

const set = (name: string, value: string | null) => (value === null ? name : `${name}: ${value}`);

export const departmentChipText = (name: string | null) => set(FILTER_TEXT.department, name);
export const ownerChipText = (name: string | null) => set(FILTER_TEXT.owner, name);
export const partyChipText = (party: string | null) => set(FILTER_TEXT.party, party);
export const propertyChipText = (property: string | null) => set(FILTER_TEXT.property, property);

/** "Label", "Label: Confidential", "Labels: Confidential, Restricted". */
export function labelsChipText(labels: readonly Sensitivity[]): string {
  if (labels.length === 0) return FILTER_TEXT.label;
  const names = labels.map(labelName).join(", ");
  return labels.length === 1 ? `Label: ${names}` : `Labels: ${names}`;
}

/** The card's date (Q3), both days included: "Dated 1 Jan 2026 to 31 Mar 2026". */
export function documentDateChipText(from: string | null, to: string | null): string {
  if (from !== null && to !== null) return `Dated ${formatDate(from)} to ${formatDate(to)}`;
  if (from !== null) return `Dated from ${formatDate(from)}`;
  if (to !== null) return `Dated until ${formatDate(to)}`;
  return FILTER_TEXT.documentDate;
}
