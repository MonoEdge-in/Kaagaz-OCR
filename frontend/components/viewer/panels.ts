// The words and spots behind the viewer's panels (PRD 3 Y1 to Y4), kept out of
// the components so they are unit tested in node (tests/viewer-panels.test.ts):
// the card's items, the related documents' sentence, the invoice's check chip
// and cell spots, and the low-resolution warning.

import { fieldText } from "@/components/viewer/labels";
import { sameRegion } from "@/components/viewer/selection";
import type {
  DocumentCard,
  DocumentLink,
  DocumentStatus,
  ExtractedValue,
  Invoice,
  Label,
  PageSummary,
  Region,
} from "@/lib/types";

/** A spot to highlight: the item's own, and its label's when it has one (it can be on another page). */
export interface Spot {
  name: string; // said when it is shown: "Showing {name} on page n."
  region: Region;
  label: Region | null;
}

export interface CardItem extends Spot {
  key: string;
  text: string;
}

const LANGUAGES: Record<string, string> = {
  en: "English",
  hi: "Hindi",
  mr: "Marathi",
};

/** "English, Marathi"; "Not known" for none. */
export function languagesText(codes: string[]): string {
  return codes.length === 0
    ? "Not known"
    : codes.map((code) => LANGUAGES[code] ?? "Other").join(", ");
}

/** The label spot of the card value at this spot, when the detail has one. */
function labelOf(values: ExtractedValue[], field: string, region: Region): Region | null {
  const value = values.find(
    (v) => v.record_key === "card" && v.field === field && sameRegion(v.region, region),
  );
  return value?.label_region ?? null;
}

/** The card's clickable items, by section, in the card's own order; empty sections are left out. */
export function cardSections(
  card: DocumentCard,
  values: ExtractedValue[],
): { heading: string; items: CardItem[] }[] {
  const date = card.document_date;
  const sections = [
    {
      heading: "Parties",
      items: card.parties.map((party, i) => ({
        key: `party-${i}`,
        name: fieldText(party.role),
        text: party.name_latin ? `${party.name} (${party.name_latin})` : party.name,
        region: party.region,
        label: labelOf(values, party.field, party.region),
      })),
    },
    {
      heading: "Dates",
      items: date
        ? [
            {
              key: "date",
              name: "Document date",
              text: date.value,
              region: date.region,
              label: null,
            },
          ]
        : [],
    },
    {
      heading: "Identifiers",
      items: card.identifiers.map((id, i) => ({
        key: `id-${i}`,
        name: fieldText(id.kind),
        text: id.value,
        region: id.region,
        label: labelOf(values, id.field, id.region),
      })),
    },
    {
      heading: "Key facts",
      items: card.key_facts.map((fact, i) => ({
        key: `fact-${i}`,
        name: fieldText(fact.field),
        text: fact.value,
        region: fact.region,
        label: fact.label_region,
      })),
    },
  ];
  return sections.filter((section) => section.items.length > 0);
}

/** As links.normalise_identifier stores it: no spaces, lower case, Devanagari digits as ASCII. */
export function normaliseIdentifier(text: string): string {
  return text
    .replace(/\s/g, "")
    .toLowerCase()
    .replace(/[०-९]/g, (digit) => String(digit.charCodeAt(0) - 0x0966));
}

/**
 * "Same invoice number: INV-2026-0914". The value is spelled as this document
 * writes it, when one of its own values matches the stored (normalised) one.
 */
export function linkText(link: DocumentLink, values: ExtractedValue[]): string {
  const { kind, value } = link.identifier;
  const own = values.find(
    (v) =>
      v.field === kind &&
      (v.record_key === "card" || v.record_key === "document") &&
      normaliseIdentifier(v.value) === value,
  );
  const words = kind === "po_number" ? "PO number" : fieldText(kind).toLowerCase();
  return `Same ${words}: ${own?.value ?? value}`;
}

const CHECK_REASONS: Record<string, string> = {
  lines_sum_mismatch: "line items don't add up to the subtotal",
  total_mismatch: "the subtotal and tax don't add up to the total",
  missing_invoice_number: "no invoice number was found",
  missing_total: "no total was found",
  bad_date: "the invoice date doesn't look right",
  bad_gstin: "a GSTIN isn't valid",
  conflicting_values: "a field was read with two different values",
  truncated: "the invoice was too long to read in full",
};

/** One invoice check reason in plain words. */
export function checkReasonText(reason: string): string {
  return CHECK_REASONS[reason] ?? fieldText(reason).toLowerCase();
}

/** "Checks passed", or "Needs review: …" with every reason; `label` gives its look (LabelBadge's). */
export function checkChip(invoice: Pick<Invoice, "check_status" | "check_reasons">): {
  text: string;
  label: Label;
} {
  if (invoice.check_status === "passed") return { text: "Checks passed", label: "verified" };
  if (invoice.check_status === "not_checked") {
    return {
      text: "Needs review: the amounts couldn't be checked",
      label: "needs_review",
    };
  }
  return {
    text: `Needs review: ${invoice.check_reasons.map(checkReasonText).join("; ")}`,
    label: "needs_review",
  };
}

/** The invoice fields in screen order, with the field their value is stored under (E4 names). */
export const INVOICE_FIELDS = [
  ["invoice_number", "invoice_number"],
  ["invoice_date", "invoice_date"],
  ["seller_name", "party_name"],
  ["seller_gstin", "gstin"],
  ["buyer_name", "buyer_name"],
  ["buyer_gstin", "buyer_gstin"],
  ["subtotal", "subtotal"],
  ["tax_total", "tax_amount"],
  ["total", "total_amount"],
] as const;

export const LINE_FIELDS = ["description", "hsn", "quantity", "unit", "rate", "amount"] as const;

/** Where an invoice value sits: record "document" for a field, "line:{n}" for a line's cell. */
export function invoiceSpot(values: ExtractedValue[], record: string, field: string): Spot | null {
  const value = values.find((v) => v.record_key === record && v.field === field);
  return value
    ? {
        name: fieldText(field),
        region: value.region,
        label: value.label_region,
      }
    : null;
}

/** The warning for a page scanned at a low resolution (PRD 3 Y4); null otherwise. */
export function lowResolutionText(page: Pick<PageSummary, "low_resolution" | "effective_dpi">) {
  if (!page.low_resolution || page.effective_dpi === null) return null;
  return `This page was scanned at ${Math.round(page.effective_dpi)} DPI. Small text may be misread. Upload a sharper scan if values look wrong.`;
}

/** "Page 2", or "Value on page 2, label on page 1" when its label sits on another page. */
export function spotNote(spot: Spot): string {
  const label = spot.label?.page;
  const page = spot.region.page;
  return label === undefined || label === page
    ? `Page ${page}`
    : `Value on page ${page}, label on page ${label}`;
}

/** Why there is no card (PRD 2 D6): not read yet, or read with the AI model off or out of reach. */
export function noCardText(status: DocumentStatus): string {
  return status === "ready"
    ? "No document card. The AI model was off or could not be reached when this was read."
    : "The document card comes once the document is read.";
}
