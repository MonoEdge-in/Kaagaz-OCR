// Admin → Answer quality → What Kaagaz learned (PRD 6 F3), rendered in node from fixtures
// shaped like GET /admin/learned: its states, no question or name, links from ids only.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LEARNED_TEXT, LearnedView, peopleText } from "@/components/admin/LearnedList";
import { describeAction } from "@/components/admin/logic";
import type { LearnedDocument } from "@/lib/types";

const noop = () => undefined;
const view = (items: LearnedDocument[] | null, error: string | null = null) =>
  renderToStaticMarkup(createElement(LearnedView, { items, error, onRetry: noop, onRemove: noop }));

const ITEM: LearnedDocument = {
  document_id: "doc-1",
  document_name: "<b>March bill</b>.pdf",
  people: 2,
  last_marked_at: "2026-10-22T05:00:00Z",
};

describe("What Kaagaz learned", () => {
  it("loads, fails with Try again, and says when nothing was learned", () => {
    expect(view(null)).toContain(LEARNED_TEXT.loading);
    const failed = view(null, "Could not reach the server. Check your connection, then try again.");
    expect(failed).toContain(LEARNED_TEXT.failed);
    expect(failed).toContain("Try again");
    expect(view([])).toContain("Nothing learned yet.");
  });

  it("lists each document with how many people and when, and Remove; no question, no name", () => {
    const html = view([ITEM, { ...ITEM, document_id: "doc-2", people: 1 }]);
    // Per document, on any question: never read as the per-page, same-words count the lift uses.
    expect(html).toContain("2 people, on any question");
    expect(html).toContain("1 person, on any question");
    expect(LEARNED_TEXT.intro).toContain("2 or more people have marked the same page for similar questions");
    expect(html).toContain("22 Oct 2026");
    expect([...html.matchAll(/<button[^>]*>Remove</g)]).toHaveLength(2);
    expect(html).not.toContain("<b>March");
    for (const [, href] of html.matchAll(/href="([^"]*)"/g)) expect(href).toMatch(/^\/documents\/doc-[12]$/);
    expect(html).toMatch(/data-label="Marked Useful by"/); // the cards below 48rem say what each value is
    expect(peopleText(1)).toBe("1 person, on any question");
  });

  it("names its audit row in plain words", () => {
    expect(describeAction("learning.remove")).toBe("Removed what Kaagaz learned about a document");
  });
});
