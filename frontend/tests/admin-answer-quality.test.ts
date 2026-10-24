// Admin → Answer quality (PRD 6 F2), rendered in node from fixtures shaped like
// GET /admin/answer-quality: every state, one answer and 200, plain text only, the read-only
// answer, and the cards' layout rules.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerQualityView, QUALITY_PAGE_SIZE, QUALITY_TEXT, reasonsText } from "@/components/admin/AnswerQualityPanel";
import { describeAction, describeTarget } from "@/components/admin/logic";
import type { AnswerQualityItem, AnswerQualityReply } from "@/lib/types";

const noop = () => undefined;

const item = (n: number, changes: Partial<AnswerQualityItem> = {}): AnswerQualityItem => ({
  id: `f${n}`,
  message_id: `m${n}`,
  asked_by: "Ravi Sharma",
  question: "What is the March total?",
  answer: {
    status: "answered",
    label: "verified",
    sentences: [{ text: "The total is 1,200.", citations: [1] }],
    citations: [
      {
        n: 1,
        kind: "piece",
        ref_id: "p1",
        document_id: "doc-1",
        document_name: "March bill.pdf",
        detected_type: "pdf",
        page_no: 2,
        regions: [],
        snippet: "Total 1,200",
        label: "verified",
      },
    ],
    closest_documents: [],
    route: "search",
    normalized_question: "march total",
    model_used: true,
  },
  reason_codes: ["wrong_figure", "other"],
  reason: "It was the April bill.",
  given_at: "2026-10-22T05:00:00Z",
  handled_at: null,
  ...changes,
});

const reply = (items: AnswerQualityItem[], total = items.length): AnswerQualityReply => ({
  from: "2026-09-28",
  weeks: [
    { week: "2026-10-19", useful: 12, not_useful: 3 },
    { week: "2026-10-12", useful: 0, not_useful: 0 },
  ],
  items,
  total,
});

const view = (props: Partial<Parameters<typeof AnswerQualityView>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(AnswerQualityView, {
      status: "open",
      weeks: "4",
      offset: 0,
      reply: null,
      error: null,
      onChoose: noop,
      onRetry: noop,
      onHandle: noop,
      ...props,
    }),
  );

describe("Answer quality", () => {
  it("says it is loading, with grey blocks the size of the cards", () => {
    const html = view();
    expect(html).toContain(QUALITY_TEXT.loading);
    expect(html).toContain('class="skeleton');
  });

  it("says what failed and offers Try again", () => {
    const html = view({ error: "Could not reach the server. Check your connection, then try again." });
    expect(html).toContain('role="alert"');
    expect(html).toContain("Could not load the answers.");
    expect(html).toContain("Try again");
    expect(html).not.toContain(QUALITY_TEXT.loading);
  });

  it("says when nothing was marked Not useful in the period, and still shows the weeks", () => {
    const html = view({ reply: reply([]) });
    expect(html).toContain("No answers marked Not useful in this period.");
    expect(html).toContain("Useful and Not useful by week");
    expect(view({ status: "handled", reply: reply([]) })).toContain("No handled answers in this period.");
  });

  it("shows the weekly counts as numbers", () => {
    const html = view({ reply: reply([]) });
    expect(html).toMatch(/<td class="tabular">12<\/td><td class="tabular">3<\/td>/);
    expect(html).toMatch(/<td class="tabular">0<\/td><td class="tabular">0<\/td>/);
  });

  it("shows who asked, the question, the answer read-only, what went wrong, their words and when", () => {
    const html = view({ reply: reply([item(1)]) });
    expect(html).toContain("Asked by Ravi Sharma");
    expect(html).toContain("What is the March total?");
    expect(html).toContain("The total is 1,200.");
    expect(html).toContain("Wrong figure, Other");
    expect(html).toContain("It was the April bill.");
    expect(html).toContain("Marked Not useful 22 Oct 2026");
    expect(html).toContain(QUALITY_TEXT.handle);
    // Someone else's answer: no rating and no Most relevant on it.
    expect(html).not.toMatch(/Was this answer useful\?|Most relevant/);
    expect(view({ status: "handled", reply: reply([item(1, { handled_at: "2026-10-23T05:00:00Z" })]) })).toContain(
      QUALITY_TEXT.reopen,
    );
    expect(reasonsText([])).toBe("No reason ticked.");
  });

  it("shows what people typed as plain text, and links only to documents by id", () => {
    const html = view({
      reply: reply([
        item(1, {
          question: '<a href="https://evil.example">click</a>',
          reason: "<script>alert(1)</script>",
          asked_by: "<img src=x onerror=alert(1)>",
        }),
      ]),
    });
    expect(html).not.toMatch(/<script|<img|<a href="https/);
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    for (const [, href] of html.matchAll(/href="([^"]*)"/g)) expect(href).toMatch(/^\/documents\/doc-1\b/);
  });

  it("draws one answer, or a page of 50 out of 200 with Previous and Next", () => {
    const cards = (html: string) => [...html.matchAll(/<article [^>]*aria-labelledby="quality-/g)];
    expect(cards(view({ reply: reply([item(1)]) }))).toHaveLength(1);
    expect(view({ reply: reply([item(1)]) })).not.toContain("Answer quality pages");
    const page = Array.from({ length: QUALITY_PAGE_SIZE }, (_, n) => item(n));
    const html = view({ reply: reply(page, 200) });
    expect(QUALITY_PAGE_SIZE).toBeLessThanOrEqual(100);
    expect(cards(html)).toHaveLength(50);
    expect(html).toContain("1–50 of 200");
    expect(html).toMatch(/<button type="button" class="btn" disabled="">Previous<\/button>/);
  });

  it("keeps long text inside the card, and is a card of one column below 48rem", () => {
    const long = "x".repeat(500);
    expect(view({ reply: reply([item(1, { reason: long, question: long })]) })).toContain(long);
    const css = readFileSync(join(__dirname, "..", "components", "admin", "AnswerQuality.module.css"), "utf8");
    const text = /\.text \{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(text).toMatch(/overflow-wrap: break-word;/);
    expect(text).toMatch(/min-width: 0;/);
    expect(css).toMatch(/\.list \{[^}]*container: quality \/ inline-size;/);
    expect(css).toMatch(/@container quality \(min-width: 48rem\) \{\s*\.card \{\s*grid-template-columns: 16rem minmax\(0, 1fr\);/);
    expect(/\.card \{([^}]*)\}/.exec(css)?.[1]).not.toMatch(/grid-template-columns/); // one column below
  });

  it("names its audit rows in plain words", () => {
    expect(describeAction("feedback.handled")).toBe("Marked a Not useful answer as handled");
    expect(describeAction("feedback.reopened")).toBe("Opened a Not useful answer again");
    expect(describeTarget("feedback")).toBe("Answer");
  });
});
