// PRD 9 F-17: Review opens on By document (the documents with open values or open word flags,
// newest first, each with its count), opening one goes to its Needs attention tab, Next document
// moves on without going back, the reason chips are one Filter menu, and nothing left says
// "Nothing to check".

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DocumentRows } from "@/components/review/ReviewDocuments";
import { REVIEW_DOCUMENTS, nextDocumentHref, openDocumentHref, toCheckText } from "@/components/review/review";
import type { DocumentSummary } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const C = "33333333-3333-4333-8333-333333333333";

const summary = (id: string, name: string, counts: Partial<DocumentSummary> = {}) =>
  ({ id, display_name: name, pages_unreadable: 0, pages_uncertain: 0, values_to_review: 0, ...counts }) as DocumentSummary;

describe("F-17 Review by document", () => {
  it("asks for the file list's Needs attention documents, newest first (no span scan)", () => {
    expect(REVIEW_DOCUMENTS).toEqual({ attention: true, limit: 50 });
  });

  it("counts each document's pages still to check and values to review", () => {
    expect(toCheckText(summary(A, "a", { pages_uncertain: 1 }))).toBe("1 page to check");
    expect(toCheckText(summary(A, "a", { pages_unreadable: 1, pages_uncertain: 2, values_to_review: 3 }))).toBe(
      "3 pages to check · 3 values to review",
    );
    expect(toCheckText(summary(A, "a", { values_to_review: 1 }))).toBe("1 value to review");
  });

  it("opens a document on its Needs attention tab, carrying the documents after it and the way back", () => {
    const href = openDocumentHref([A, B, C], 0);
    const url = new URL(href, "https://kaagaz.invalid");
    expect(url.pathname).toBe(`/documents/${A}`);
    expect(url.searchParams.get("tab")).toBe("attention");
    expect(url.searchParams.get("next")).toBe(`${B},${C}`);
    expect(url.searchParams.get("from")).toBe("/review");
    expect(new URL(openDocumentHref([A, B, C], 2), "https://kaagaz.invalid").searchParams.get("next")).toBeNull();
  });

  it("moves on to the next document without going back, and stops at the last", () => {
    const next = new URL(nextDocumentHref(`${B},${C}`) ?? "", "https://kaagaz.invalid");
    expect(next.pathname).toBe(`/documents/${B}`);
    expect(next.searchParams.get("tab")).toBe("attention");
    expect(next.searchParams.get("next")).toBe(C);
    expect(nextDocumentHref(null)).toBeNull();
    expect(nextDocumentHref("")).toBeNull();
    // Only document ids from the address: anything else is dropped, never put in a link.
    expect(nextDocumentHref("../admin,javascript:alert(1)")).toBeNull();
    expect(new URL(nextDocumentHref(`x/y,${C}`) ?? "", "https://kaagaz.invalid").pathname).toBe(`/documents/${C}`);
  });

  it("lists each document with its count and a link to its Needs attention tab", () => {
    const html = renderToStaticMarkup(
      createElement(DocumentRows, {
        documents: [summary(A, "challan.pdf", { pages_uncertain: 1 }), summary(B, "bill.pdf", { values_to_review: 2 })],
        total: 2,
      }),
    );
    expect(html).toContain("challan.pdf");
    expect(renderToStaticMarkup(createElement(DocumentRows, { documents: [summary(A, "सातबारा.pdf")], total: 1 }))).toContain(
      '<span lang="mr">सातबारा</span>',
    );
    expect(html).toContain("1 page to check");
    expect(html).toContain("2 values to review");
    expect(html).toContain(`href="/documents/${A}?tab=attention&amp;next=${B}&amp;from=%2Freview"`);
    expect(html).toContain(`href="/documents/${B}?tab=attention&amp;from=%2Freview"`);
    expect(html.indexOf("challan.pdf")).toBeLessThan(html.indexOf("bill.pdf")); // the server's order: newest first
  });

  it('says "Nothing to check" when nothing is left', () => {
    expect(renderToStaticMarkup(createElement(DocumentRows, { documents: [], total: 0 }))).toContain("Nothing to check");
  });

  it("says when more documents wait than are shown", () => {
    const html = renderToStaticMarkup(createElement(DocumentRows, { documents: [summary(A, "a.pdf")], total: 80 }));
    expect(html).toContain("The newest 1 of 80.");
    expect(html).toContain('href="/documents?attention=1"');
  });

  it("opens on By document unless the address asks for one document's or one reason's values", () => {
    const queue = read("components/review/ReviewQueue.tsx");
    expect(queue).toContain('useState<QueueView>(documentId || initialReason ? "focus" : "document")');
    expect(queue).toContain('{view === "document" ? <ReviewDocuments /> : null}');
    expect(queue).toContain("if (!values) return; // By document asks for its own list");
  });

  it("puts the reason chips in one Filter menu, the existing RowMenu", () => {
    const queue = read("components/review/ReviewQueue.tsx");
    expect(queue).not.toContain("k-chip-row");
    expect(queue).toMatch(/<RowMenu\s+name="the reasons"\s+label=\{`Filter: \$\{reasonText\(reason\)\}`\}/);
  });

  it("opens the document on the tab the link names, and shows Next document from Review", () => {
    expect(read("app/(app)/documents/[id]/page.tsx")).toContain('tabParam={search.get("tab")}');
    expect(read("app/(app)/documents/[id]/page.tsx")).toContain('nextParam={search.get("next")}');
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).toContain(': tabParam === "attention" ? "attention" : "overview"');
    expect(view).toContain("const next = nextDocumentHref(nextParam);");
  });
});
