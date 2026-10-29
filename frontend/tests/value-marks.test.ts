// The owner, 29 Oct 2026: an amber "Ready to verify" pill on every fact made the product look
// unsure, and file names and values were underlined. A value's label is now a quiet mark (its
// word read out and in a tooltip), the summary card says once how many are not checked yet, and
// names and values in lists are links by weight and colour, underlined on hover and focus only.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SUMMARY_TEXT, SummaryCard } from "@/app/(app)/documents/[id]/SummaryCard";
import { DocumentsGrid } from "@/components/files/DocumentsGrid";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { labelMarkIcon, labelText, labelTip } from "@/components/viewer/labels";
import type { DocumentCard, DocumentDetail, DocumentSummary, ExtractedValue, Region } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const GLOBALS = css("app/globals.css");
/** The body of the first rule whose selector is exactly `selector`, after `from` in the text. */
const rule = (text: string, selector: string, from = 0) => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\:]/g, "\\$&");
  return new RegExp(`(?:^|\\n|\\})\\s*${escaped} \\{([^}]*)\\}`).exec(text.slice(from))?.[1] ?? "";
};
const mark = (label: "verified" | "needs_review") => renderToStaticMarkup(createElement(LabelBadge, { label }));
/** What is shown on screen: the markup without its screen-reader text and its hidden tooltip. */
const seen = (html: string) =>
  html
    .replace(/<span class="visually-hidden">[^<]*<\/span>/g, "")
    .replace(/<span[^>]*role="tooltip"[^>]*>[^<]*<\/span>/g, "")
    .replace(/<[^>]+>/g, "");

describe("a value's label mark", () => {
  it("names each label for screen readers, shows no word on screen, and tells them apart by shape", () => {
    const verified = mark("verified");
    const waiting = mark("needs_review");
    expect(verified).toContain('<span class="visually-hidden">Verified</span>');
    expect(waiting).toContain('<span class="visually-hidden">Ready to verify</span>');
    expect(seen(verified)).toBe("");
    expect(seen(waiting)).toBe("");
    expect(verified).toContain('class="label-mark label-mark-verified"');
    expect(waiting).toContain('class="label-mark"');
    expect([labelMarkIcon("verified"), labelMarkIcon("needs_review")]).toEqual(["check-seal", "ring-dashed"]);
    const drawing = (html: string) => /<svg[\s\S]*<\/svg>/.exec(html)?.[0];
    expect(drawing(verified)).not.toEqual(drawing(waiting));
    // The words stay the product's own (lib/status.ts, docs/architecture.md).
    expect([labelText("verified"), labelText("needs_review")]).toEqual(["Verified", "Ready to verify"]);
  });

  it("reaches its tooltip by keyboard and describes itself by it, in plain words with the reason when known", () => {
    for (const label of ["verified", "needs_review"] as const) {
      const html = mark(label);
      const id = /tabindex="0" aria-describedby="([^"]+)"/.exec(html)?.[1];
      expect(id, label).toBeTruthy();
      expect(html).toContain(`id="${id}" role="tooltip"`);
    }
    expect(labelTip("verified")).toBe("Verified.");
    expect(labelTip("needs_review")).toBe("Ready to verify: not checked yet.");
    expect(labelTip("needs_review", "readers_disagree")).toBe("Ready to verify: not checked yet. The readers read different values.");
    expect(labelTip("verified", "direct_cell")).toBe("Verified. Read straight from a spreadsheet cell.");
  });

  it("is a quiet mark in tokens: no pill, fill or edge; the secondary ink or the success colour; 44px on touch", () => {
    const body = rule(GLOBALS, ".label-mark");
    expect(body).toMatch(/color:\s*var\(--text-secondary\);/);
    expect(body).toMatch(/min-height:\s*var\(--badge-h\);/);
    expect(body).not.toMatch(/background|border:|#[0-9a-f]{3,8}\b/i);
    expect(rule(GLOBALS, ".label-mark-verified")).toMatch(/^\s*color:\s*var\(--success\);\s*$/);
    expect(GLOBALS).toMatch(/@media \(pointer: coarse\) \{\s*\.label-mark \{\s*min-width: var\(--h-lg\);\s*min-height: var\(--h-lg\);/);
  });

  it("keeps the Review queue's explicit pill, where people verify", () => {
    expect(read("components/review/ReviewCard.tsx")).toContain('<Tip className="badge badge-review"');
  });
});

const at = (y: number): Region => ({ kind: "box", page: 1, box: [0.1, y, 0.5, y + 0.02] });
const value = (field: string, y: number, label: "verified" | "needs_review") =>
  ({ id: field, field, value: field, raw: field, label, label_reason: "readers_agree", region: at(y) }) as ExtractedValue;
const card: DocumentCard = {
  version: 1,
  run_id: null,
  model: "x",
  prompt: "p",
  doc_type: "land_record",
  languages: ["en"],
  title: null,
  summary: "A sale deed.",
  document_date: { value: "15/06/2007", date: "2007-06-15", page: 1, region: at(0.3) },
  parties: [{ role: "seller", field: "seller_name", name: "Jalna Municipal Council", name_latin: null, page: 1, region: at(0.1) }],
  identifiers: [{ kind: "reference_number", field: "reference_number", value: "1918", page: 1, region: at(0.4) }],
  key_facts: [],
  dropped_quotes: 0,
};
const detail = (values: ExtractedValue[]) =>
  ({ id: "d 1", status: "ready", understanding: false, card, values, pages: [] }) as unknown as DocumentDetail;
const summary = (values: ExtractedValue[], reviewer: boolean) =>
  renderToStaticMarkup(createElement(SummaryCard, { detail: detail(values), active: null, onShow: () => undefined, reviewer }));

describe("the summary card's one line about checking", () => {
  // Seller verified; Reference number waiting; Document date has no value behind it, so it waits too.
  const values = [value("seller_name", 0.1, "verified"), value("reference_number", 0.4, "needs_review")];

  it("counts the facts not checked yet once, and links a reviewer to this document in Review", () => {
    const html = summary(values, true);
    expect(SUMMARY_TEXT.toVerify(2)).toBe("2 values to verify");
    expect(html).toContain(`<span>${SUMMARY_TEXT.toVerify(2)}</span>`);
    expect(html).toContain('<a href="/review?document_id=d%201">Review</a>');
    expect(html.match(/class="label-mark"/g)).toHaveLength(2);
    expect(html.match(/label-mark-verified/g)).toHaveLength(1);
    expect(html).not.toContain("badge");
  });

  it("tells a member the count, with no link", () => {
    const html = summary(values, false);
    expect(SUMMARY_TEXT.notChecked(1)).toBe("1 value not checked yet");
    expect(html).toContain(`<span>${SUMMARY_TEXT.notChecked(2)}</span>`);
    expect(html).not.toContain("/review");
  });

  it("says nothing when every fact is verified, and keeps the AI caution", () => {
    const all = [value("seller_name", 0.1, "verified"), value("reference_number", 0.4, "verified")];
    const dated = { ...value("document_date", 0.3, "verified") };
    const html = summary([...all, dated], true);
    expect(html).not.toContain("to verify");
    expect(html).not.toContain("/review");
    expect(html).toContain(SUMMARY_TEXT.caution);
  });
});

describe("quiet links: file names and values in lists", () => {
  it("are not underlined at rest, and are on keyboard focus and on hover with a mouse", () => {
    expect(rule(GLOBALS, ".quiet-link")).toMatch(/text-decoration:\s*none;/);
    expect(rule(GLOBALS, ".quiet-link:focus-visible")).toMatch(/text-decoration:\s*underline;/);
    expect(GLOBALS).toMatch(/@media \(hover: hover\) and \(pointer: fine\) \{\s*\.quiet-link:hover \{\s*text-decoration: underline;/);
    // The focus ring stays the shared one.
    expect(GLOBALS).toMatch(/:focus-visible \{\s*outline: 2px solid var\(--focus\);/);
  });

  it("are the grid's and list's file names, the search results' names and the summary card's values", () => {
    const grid = renderToStaticMarkup(
      createElement(DocumentsGrid, {
        items: [{ id: "a", display_name: "Plots Title of Panchamukhi Nagar.pdf", version_no: 1, created_at: "2026-10-28T00:00:00Z", doc_type: null } as unknown as DocumentSummary],
        label: "Documents",
      }),
    );
    expect(grid).toMatch(/<a class="quiet-link [^"]*cardName[^"]*" href="\/documents\/a">Plots Title of Panchamukhi Nagar\.pdf<\/a>/);
    expect(read("components/documents/DocumentsTable.tsx")).toContain("className={`quiet-link ${styles.name}`}");
    expect(read("components/documents/InsideResults.tsx")).toContain("className={`quiet-link ${styles.resultName}`}");
    const values = summary([], false);
    expect(values.match(/<button type="button" class="quiet-link [^"]*valueButton/g)).toHaveLength(3);
    // The value button no longer draws its own underline.
    expect(rule(css("app/(app)/documents/[id]/DocumentView.module.css"), ".valueButton")).not.toMatch(/text-decoration/);
  });

  it("leaves links inside sentences underlined (the global link rule)", () => {
    expect(rule(GLOBALS, "a")).not.toMatch(/text-decoration/);
  });
});
