// PRD 6 package A (24 Oct 2026): the answer card for contract v2. The opening, the short
// answer, the documents under it (title, why, the figure as printed with its label, the
// page), what was assumed, and the soft not-found with its closest documents; what the
// status line says; and the layout rules from the build spec (no overflow at 320px:
// chips of their own, figures on one line, Devanagari marked, flex text children that
// may shrink). Static renders, as chat-answer.test.ts and layout-guards.test.ts do.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerView, OUTAGE_TEXT, TIMEOUT_TEXT, TRY_THESE } from "@/components/chat/AnswerView";
import {
  answerCardId,
  answerStatusText,
  figureOf,
  listedDocuments,
  NOT_FOUND_CLOSEST_TEXT,
  NOT_FOUND_TEXT,
  notFoundText,
  scriptParts,
} from "@/components/chat/answer";
import type { Answer, Citation, Message } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const text = (html: string) =>
  html.replace(/<[^>]*>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

const value = (n: number, documentId: string, overrides: Partial<Citation> = {}): Citation => ({
  n,
  kind: "value",
  ref_id: `v${n}`,
  document_id: documentId,
  document_name: `${documentId}.pdf`,
  detected_type: "pdf",
  page_no: 2,
  regions: [{ kind: "box", page: 2, box: [0.1, 0.2, 0.3, 0.4] }],
  snippet: "total_amount: ₹1,23,45,678.00",
  label: "needs_review",
  ...overrides,
});

/** A v2 answer listing `count` documents, each citing one printed figure. */
function v2(count: number, overrides: Partial<Answer> = {}): Answer {
  const ids = Array.from({ length: count }, (_, i) => `d${i + 1}`);
  return {
    version: 2,
    status: "answered",
    label: "needs_review",
    opening: "Good evening, Vrushit. Here's what I found.",
    sentences: [{ text: "The highest total amount was 1,23,45,678.", citations: ids.map((_, i) => i + 1) }],
    citations: ids.map((id, i) => value(i + 1, id, { label: i === 0 ? "verified" : "needs_review" })),
    closest_documents: [],
    documents: ids.slice(0, 10).map((id, i) => ({
      document_id: id,
      title: `Invoice INV-${2041 + i}`,
      why: "Total amount as printed",
      citations: [i + 1],
    })),
    assumptions: [],
    route: "fixed_query",
    normalized_question: "highest total",
    model_used: false,
    ...overrides,
  };
}

const message = (answer: Answer | null): Message => ({
  id: "a1",
  role: "assistant",
  text: "Saved text.",
  created_at: "2026-10-24T13:00:00Z",
  answer,
  feedback: null,
});
const render = (answer: Answer | null) =>
  renderToStaticMarkup(createElement(AnswerView, { message: message(answer), question: "highest total?" }));
const listItems = (html: string) => html.match(/<li class="[^"]*docItem[^"]*"/g)?.length ?? 0;

describe("a v2 answer card", () => {
  it("opens, answers in short, then lists 0, 1, 5 or the first 10 of 50 documents and counts the rest", () => {
    for (const [count, shown, more] of [
      [0, 0, ""],
      [1, 1, ""],
      [5, 5, ""],
      [50, 10, "40 more documents are in Sources."],
    ] as const) {
      const html = render(v2(count));
      expect(listItems(html), String(count)).toBe(shown);
      expect(text(html)).toContain("Good evening, Vrushit. Here's what I found.");
      expect(text(html)).toContain("The highest total amount was 1,23,45,678.");
      if (more) expect(text(html)).toContain(more);
      else expect(html).not.toContain("more documents are in Sources");
    }
    const listed = listedDocuments(v2(50));
    expect([listed.documents.length, listed.more]).toEqual([10, 40]);
  });

  it("shows each figure as printed with its label beside it, never on the title, and the page to open", () => {
    const html = render(v2(2));
    expect(figureOf(value(1, "d1"))).toBe("₹1,23,45,678.00");
    expect(figureOf({ ...value(1, "d1"), kind: "piece" })).toBeNull();
    const item = html.slice(html.indexOf("Invoice INV-2041"), html.indexOf("Invoice INV-2042"));
    // Changed on purpose (owner, 29 Oct 2026): the label is a quiet mark, not a pill (LabelBadge).
    expect(item).toMatch(/<span class="[^"]*figure[^"]*">₹1,23,45,678\.00<\/span>\s*<span class="tip-wrap"><span class="label-mark label-mark-verified"/);
    // Changed on purpose (Vrushit UAT 3, 27 Oct 2026): the title opens the cited spot, and each
    // page is a small "p. 2" link on the title's line; no row of page chips.
    expect(item).toContain('href="/documents/d1?page=2');
    expect(item).toContain("p. 2");
    expect(item).not.toContain("Page 2");
    // The title line holds no badge; the total in the short answer has the answer's label (its
    // mark: changed on purpose, owner, 29 Oct 2026).
    expect(html).toMatch(/<p class="[^"]*docTitle[^"]*"><a class="[^"]*docLink[^"]*" href="\/documents\/d1\?page=2[^"]*">Invoice INV-2041<\/a>/);
    expect(html).not.toMatch(/docTitle[^"]*"><a[^>]*>[^<]*<\/a>\s*<span class="(badge|tip-wrap)/);
    expect(html).toMatch(/1,23,45,678\.<a[\s\S]*?<\/a><\/span> <span class="tip-wrap"><span class="label-mark"/);
    expect(text(html)).toContain("Ready to verify");
    expect(text(html)).not.toMatch(/needs review/i);
  });

  it("never shows a hard-to-read figure as Verified, even when its value is", () => {
    const answer = v2(1);
    answer.citations = [value(1, "d1", { label: "verified", hard_to_read: true })];
    const html = render(answer);
    const item = html.slice(html.indexOf("Invoice INV-2041"));
    // Changed on purpose (owner, 29 Oct 2026): the label is a quiet mark, not a pill (LabelBadge).
    expect(item).toMatch(/<span class="[^"]*figure[^"]*">₹1,23,45,678\.00<\/span>\s*<span class="tip-wrap"><span class="label-mark"/);
    expect(item).not.toContain("label-mark-verified");
  });

  it("says what was assumed in one line", () => {
    const html = render(v2(1, { assumptions: ["I took September as September 2026."] }));
    expect(text(html)).toContain("I took September as September 2026.");
  });

  it("marks Devanagari as Marathi, only where it is", () => {
    expect(scriptParts("Owner कृष्णा शिंदे, mentions land")).toEqual([
      { text: "Owner " },
      { text: "कृष्णा शिंदे", lang: "mr" },
      { text: ", mentions land" },
    ]);
    const html = render(v2(1, { documents: [{ document_id: "d1", title: "७/१२ उतारा", why: "Owner कृष्णा", citations: [1] }] }));
    expect(html).toContain('<span lang="mr">७/१२ उतारा</span>');
    expect(html).toContain('Owner <span lang="mr">कृष्णा</span>');
  });

  it("is a focus target with its own id, so focus moves to a new answer", () => {
    expect(render(v2(1))).toMatch(new RegExp(`<div[^>]*id="${answerCardId("a1")}"[^>]*tabindex="-1"|tabindex="-1"[^>]*id="${answerCardId("a1")}"`));
    const screen = read("components/chat/ChatScreen.tsx");
    expect(screen).toContain("focus({ preventScroll: true })");
    expect(screen).toContain("getElementById(answerCardId(messageId))");
    expect(screen.match(/focusAnswer\(reply\.answer\.id\)/g)).toHaveLength(2); // a new answer, and one finished while away
  });
});

describe("a v2 not-found answer", () => {
  const notFound = (closest: number): Answer =>
    v2(0, {
      status: "not_found",
      label: null,
      opening: "Sure.",
      sentences: [],
      citations: [],
      closest_documents: Array.from({ length: closest }, (_, i) => ({ id: `c${i}`, display_name: `c${i}.pdf` })),
      documents: Array.from({ length: closest }, (_, i) => ({
        document_id: `c${i}`,
        title: `Rent agreement ${i}`,
        why: "Mentions godown",
        citations: [],
      })),
      route: "search",
    });

  it("says the soft line, lists up to 5 closest documents to open, and keeps the next steps", () => {
    const html = render(notFound(5));
    expect(text(html)).toContain(`Sure. ${NOT_FOUND_CLOSEST_TEXT}`);
    expect(listItems(html)).toBe(5);
    // Changed on purpose (#413, Vrushit's UAT): the first 3 show; the rest wait, closed, in a
    // "2 more documents" disclosure, as the Sources bar does.
    const shown = html.slice(0, html.indexOf("<details"));
    expect(listItems(shown)).toBe(3);
    expect(html).toMatch(/<details class="[^"]*moreDocs[^"]*"><summary[^>]*>[\s\S]*?2 more documents<\/summary>/);
    expect(listItems(html.slice(html.indexOf("<details")))).toBe(2);
    expect(render(notFound(3))).not.toContain("<details");
    // Changed on purpose (package H, 24 Oct 2026): a closest document opens with the question's words lit.
    expect(html).toContain('href="/documents/c0?words=highest+total"');
    expect(text(html)).toContain("Mentions godown");
    expect(text(html)).toContain("Next steps");
    expect(text(html)).not.toMatch(/sorry|couldn't/i);
    const none = render(notFound(0));
    expect(text(none)).toContain(NOT_FOUND_TEXT);
    expect(listItems(none)).toBe(0);
  });

  // #410 (Vrushit's UAT): the words of the question no document has are named, as voice.py says
  // them (test_answering_voice.py pins the same lines).
  it("names the words no document searched has", () => {
    const html = render({ ...notFound(2), missing_words: ["SBH"] });
    expect(text(html)).toContain(
      "Sure. No document searched has the word “SBH”. These documents match some of your words.",
    );
    expect(notFoundText(0, ["SBH", "Jalna", "Wakad"])).toBe("No document searched has the words “SBH”, “Jalna” or “Wakad”.");
    expect(notFoundText(2)).toBe(NOT_FOUND_CLOSEST_TEXT);
    expect(text(render(notFound(1)))).not.toContain("No document searched");
  });

  it("is announced softly, with how many closest documents, never the opening", () => {
    expect(answerStatusText(notFound(3))).toBe("Nothing matches exactly. 3 closest documents.");
    expect(answerStatusText(notFound(1))).toBe("Nothing matches exactly. 1 closest document.");
    expect(answerStatusText(notFound(0))).toBe(NOT_FOUND_TEXT);
  });
});

describe("what the status line says for a v2 answer", () => {
  it("gives the first sentence and how many documents, never the opening", () => {
    expect(answerStatusText(v2(2))).toBe("Answer ready. The highest total amount was 1,23,45,678. 2 documents.");
    expect(answerStatusText(v2(50))).toBe("Answer ready. The highest total amount was 1,23,45,678. 50 documents.");
    expect(answerStatusText(v2(0))).toBe("Answer ready. The highest total amount was 1,23,45,678.");
  });
});

// Every shape an answer was saved in before PRD 6 still renders, as before: no opening, no
// documents list, no label.
describe("answers saved before v2", () => {
  const piece = (n: number): Citation => ({ ...value(n, "d1"), kind: "piece", snippet: "Total 1,234", label: "verified" });
  const base: Answer = {
    status: "answered",
    label: "verified",
    sentences: [{ text: "The total is 1,234.", citations: [1] }],
    citations: [piece(1)],
    closest_documents: [],
    route: "search",
    normalized_question: "total",
    model_used: false,
  };
  const shapes: [string, Answer | null][] = [
    ["no answer", null],
    ["before PRD 3 (no pages, no rewrite)", { ...base, citations: [{ ...piece(1), page_from: undefined, page_to: undefined }] }],
    ["PRD 3", { ...base, rewritten_question: "total of bill 12", understood_words: [] }],
    ["PRD 5 F6", { ...base, reading_note: null, unreadable_pages: [], unreadable_page_count: 0 }],
    ["16 Oct, Also in and agents", { ...base, related_documents: [], agents: ["Gemini"], agreement: null }],
    ["17 Oct, Also mentions", { ...base, mentions: { terms: ["Jalna"], total: 0, documents: [] } }],
    ["not found", { ...base, status: "not_found", label: null, sentences: [], citations: [], closest_documents: [{ id: "d9", display_name: "d9.pdf" }] }],
    ["unchecked", { ...base, status: "unchecked", label: "needs_review", sentences: [], citations: [], closest_documents: [{ id: "d9", display_name: "d9.pdf" }] }],
  ];

  it.each(shapes)("renders %s as before", (_, answer) => {
    const html = render(answer);
    expect(html).not.toMatch(/docItem|class="[^"]*opening/);
    expect(text(html)).not.toMatch(/Ready to verify|Verified/);
    if (answer?.status === "answered") expect(text(html)).toContain("The total is 1,234.");
    if (answer === null) expect(text(html)).toContain("Saved text.");
  });
});

describe("layout rules for the answer card (build spec, 320px)", () => {
  const css = read("components/chat/Chat.module.css").replace(/\/\*[\s\S]*?\*\//g, "");
  const body = (selector: string) => {
    const at = css.search(new RegExp(`(^|\\n)${selector.replace(".", "\\.")}\\s*\\{`));
    expect(at, selector).toBeGreaterThanOrEqual(0);
    return css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
  };

  it("gives chips their own class, never .btn: wrapping, never wider than the card, 44px on touch", () => {
    expect(body(".chip")).toMatch(/flex:\s*0 1 auto/);
    expect(body(".chip")).toMatch(/max-width:\s*100%/);
    expect(body(".chip")).toMatch(/overflow-wrap:\s*break-word/);
    expect(body(".chipRow")).toMatch(/flex-wrap:\s*wrap/);
    expect(body(".chipRow")).toMatch(/gap:\s*var\(--space-2\)/);
    // Changed on purpose (PRD 6 integration, 24 Oct 2026): one chip for answers and clarify (B),
    // one control tall: --control-h is 44px on touch by itself.
    expect(body(".chip")).toMatch(/min-height:\s*var\(--control-h\)/);
    expect(read("components/chat/AnswerView.tsx")).not.toMatch(/className=\{?["`][^"`]*\bbtn\b[^"`]*chip|chip[^"`]*\bbtn\b/);
  });

  it("keeps a figure on one line and lets every text child of a flex row shrink", () => {
    expect(body(".figure")).toMatch(/white-space:\s*nowrap/);
    for (const selector of [".docItem", ".docFigures", ".docTitle", ".docWhy"]) {
      expect(body(selector), selector).toMatch(/min-width:\s*0/);
    }
    for (const selector of [".docTitle", ".docWhy", ".opening"]) {
      expect(body(selector), selector).toMatch(/overflow-wrap:\s*break-word/);
    }
  });
});

// Never "busy", greetings and one-press questions (Tushar, 24 Oct 2026).
describe("small talk, one-press questions and an outage", () => {
  const talk = (sentences: string[], suggestions: string[]): Answer =>
    v2(0, {
      status: "answered",
      label: null,
      route: "small_talk",
      sentences: sentences.map((t) => ({ text: t, citations: [] })),
      citations: [],
      documents: [],
      suggestions,
    });

  it("shows small talk as a line and a list, with questions to ask, and no sources", () => {
    const html = render(talk(["Here is what I can do for you:", "Answer questions about your documents.", "Find documents in Search."], ["Who are the owners named in the land records?"]));
    expect(text(html)).toContain("Here is what I can do for you:");
    expect(html.match(/<li>/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
    expect(text(html)).toContain(TRY_THESE);
    expect(html).toMatch(/<button type="button" class="k-suggest"[^>]*>Who are the owners named in the land records\?<\/button>/);
    expect(text(html)).not.toMatch(/Sources|Ready to verify|Next steps/);
  });

  it("offers questions under a not found with nothing close", () => {
    const html = render(v2(0, { status: "not_found", sentences: [], citations: [], documents: [], suggestions: ["Which invoice had the largest total last month?"] }));
    expect(text(html)).toContain(TRY_THESE);
    expect(text(html)).toContain("Which invoice had the largest total last month?");
  });

  it("says the AI is not responding during an outage, never not found", () => {
    const html = render(v2(0, { status: "not_found", sentences: [], citations: [], documents: [], outage: true }));
    expect(text(html)).toContain(OUTAGE_TEXT);
    expect(text(html)).not.toContain("Nothing in your documents matches this yet");
  });

  it("says a question that took too long should go to Swift or fewer documents, never ask again", () => {
    const empty = { status: "not_found" as const, sentences: [], citations: [], documents: [], outage: true };
    const html = render(v2(0, { ...empty, outage_reason: "timeout" }));
    expect(text(html)).toContain(TIMEOUT_TEXT);
    expect(text(html)).not.toContain(OUTAGE_TEXT);
    expect(TIMEOUT_TEXT).toBe("This question took too long for the AI. Try Swift, or ask about fewer documents.");
  });
});
