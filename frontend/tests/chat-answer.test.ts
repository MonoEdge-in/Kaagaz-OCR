import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerView } from "@/components/chat/AnswerView";
import { SourceCard } from "@/components/chat/SourceCard";
import {
  answerLabelText,
  answerStatusText,
  cardId,
  citationHighlights,
  citationLink,
  citationMap,
  citationRegion,
  citationSource,
  cleanQuestion,
  cleanReason,
  HARD_TO_READ_TEXT,
  NOT_FOUND_TEXT,
  questionReading,
  questionsByAnswer,
  readingNote,
  restoreAfterFailure,
  sendsOnKey,
  sentenceCitations,
  understoodAs,
} from "@/components/chat/answer";
import { decodeRegion } from "@/lib/regions";
import type { Answer, Citation, Message, Region } from "@/lib/types";

const citation = (n: number, overrides: Partial<Citation> = {}): Citation => ({
  n,
  kind: "piece",
  ref_id: `p${n}`,
  document_id: "doc-1",
  document_name: "Invoice.pdf",
  detected_type: "pdf",
  page_no: 2,
  regions: [{ kind: "box", page: 2, box: [0.1, 0.2, 0.3, 0.4] }],
  snippet: "Total 1,234",
  label: "verified",
  ...overrides,
});

describe("citations to source cards", () => {
  it("maps each number to its card", () => {
    const cards = citationMap({ citations: [citation(1), citation(2)] });
    expect(cards.get(2)?.ref_id).toBe("p2");
    expect(cards.has(3)).toBe(false);
  });

  it("shows only numbers that have a card, each once, in order", () => {
    const cards = citationMap({ citations: [citation(1), citation(2)] });
    expect(sentenceCitations({ text: "x", citations: [2, 9, 1, 2] }, cards)).toEqual([2, 1]);
  });

  it("gives every card a distinct id in the thread", () => {
    expect(cardId("m1", 1)).not.toBe(cardId("m2", 1));
    expect(cardId("m1", 1)).not.toBe(cardId("m1", 2));
  });

  it("previews and links the region on the cited page first", () => {
    const c = citation(1, {
      regions: [
        { kind: "box", page: 1, box: [0, 0, 0.1, 0.1] },
        { kind: "box", page: 2, box: [0.5, 0.5, 0.6, 0.6] },
      ],
    });
    expect(citationRegion(c)?.page).toBe(2);
    const url = new URL(citationLink(c), "http://x");
    expect(url.pathname).toBe("/documents/doc-1");
    expect(url.searchParams.get("page")).toBe("2");
    expect(decodeRegion(url.searchParams.get("region"))).toEqual(c.regions[1]);
  });

  it("merges the quoted lines on the cited page into one highlighted box", () => {
    const c = citation(1, {
      page_no: 1,
      regions: [
        { kind: "box", page: 1, box: [0.1, 0.12, 0.3, 0.13] },
        { kind: "box", page: 1, box: [0.1, 0.18, 0.4, 0.2] },
        { kind: "box", page: 2, box: [0.5, 0.5, 0.6, 0.6] },
      ],
    });
    expect(citationRegion(c)).toEqual({ kind: "box", page: 1, box: [0.1, 0.12, 0.4, 0.2] });
  });

  it("keeps a cell as it is", () => {
    const cell = { kind: "cell", page: 1, row: 2, column: 3 } as const;
    expect(citationRegion(citation(1, { page_no: 1, regions: [cell] }))).toEqual(cell);
  });

  it("links to the page alone when there is no region", () => {
    expect(citationLink(citation(1, { regions: [] }))).toBe("/documents/doc-1?page=2");
  });
});

describe("what a source card still shows of its document", () => {
  it("treats a missing state, and live, as live", () => {
    expect(citationSource(citation(1))).toBe("live");
    expect(citationSource(citation(1, { source_state: "live" }))).toBe("live");
  });

  it("keeps the link for an older version", () => {
    expect(citationSource(citation(1, { source_state: "older_version" }))).toBe("older");
  });

  it("shows nothing of a document in Trash or deleted", () => {
    const gone = { snippet: null, regions: [] };
    expect(citationSource(citation(1, { ...gone, source_state: "in_trash" }))).toBe("gone");
    expect(citationSource(citation(1, { ...gone, source_state: "deleted" }))).toBe("gone");
  });
});

describe("answer label text", () => {
  it("says Verified or Needs review in words, with a line of explanation", () => {
    expect(answerLabelText("verified").title).toBe("Verified");
    const review = answerLabelText("needs_review");
    expect(review.title).toBe("Needs review");
    expect(review.explanation.length).toBeGreaterThan(10);
  });

  it("uses the agreed not found sentence", () => {
    expect(NOT_FOUND_TEXT).toBe("I couldn't find this in your documents.");
  });
});

describe("understood as", () => {
  it("shows the reading when it differs", () => {
    expect(
      understoodAs("Kiska profit sabse jyada tha last month?", "whose profit highest last month"),
    ).toBe("whose profit highest last month");
  });

  it("hides it when only case, spaces or punctuation differ", () => {
    expect(understoodAs("What is the  Total?", "what is the total")).toBeNull();
    expect(understoodAs("anything", "  ")).toBeNull();
  });
});

describe("Searching for, under a follow-up's answer", () => {
  const rewrite = "What were the expenses in September?";

  it("shows the rewrite, and no Understood as when normalizing kept it", () => {
    const reading = questionReading("And the expenses?", {
      normalized_question: "what were the expenses in september",
      rewritten_question: rewrite,
    });
    expect(reading).toEqual({ searchingFor: rewrite, understoodAs: null });
  });

  it("shows nothing new for a question that was not rewritten", () => {
    for (const rewritten_question of [null, undefined, "  "]) {
      const reading = questionReading("Total?", { normalized_question: "total", rewritten_question });
      expect(reading).toEqual({ searchingFor: null, understoodAs: null });
    }
  });

  it("still shows Understood as for a question normalized but not rewritten", () => {
    expect(
      questionReading("Kiska profit?", { normalized_question: "whose profit" }).understoodAs,
    ).toBe("whose profit");
    expect(questionReading(null, { normalized_question: "whose profit" }).understoodAs).toBeNull();
  });
});

describe("what a source card's preview highlights", () => {
  it("adds a value's label to its spots", () => {
    const label: Region = { kind: "box", page: 2, box: [0.1, 0.1, 0.2, 0.15] };
    const c = citation(1, { kind: "value", label_regions: [label] });
    expect(citationHighlights(c)).toEqual([...c.regions, label]);
    expect(citationHighlights(citation(1))).toEqual(citation(1).regions);
  });
});

describe("questions for answers", () => {
  const message = (id: string, role: Message["role"], text: string): Message => ({
    id,
    role,
    text,
    created_at: "2026-09-27T10:00:00Z",
    answer: null,
    feedback: null,
  });

  it("pairs each answer with the question before it", () => {
    const map = questionsByAnswer([
      message("q1", "user", "first?"),
      message("a1", "assistant", ""),
      message("q2", "user", "second?"),
      message("a2", "assistant", ""),
    ]);
    expect(map.get("a1")).toBe("first?");
    expect(map.get("a2")).toBe("second?");
  });
});

describe("question box", () => {
  it("sends on Enter, not Shift+Enter or while composing", () => {
    expect(sendsOnKey({ key: "Enter", shiftKey: false, isComposing: false })).toBe(true);
    expect(sendsOnKey({ key: "Enter", shiftKey: true, isComposing: false })).toBe(false);
    expect(sendsOnKey({ key: "Enter", shiftKey: false, isComposing: true })).toBe(false);
    expect(sendsOnKey({ key: "a", shiftKey: false, isComposing: false })).toBe(false);
  });

  it("never sends on a key with a touch screen: Enter is a new line, the button sends", () => {
    expect(sendsOnKey({ key: "Enter", shiftKey: false, isComposing: false }, true)).toBe(false);
    expect(sendsOnKey({ key: "Enter", shiftKey: true, isComposing: false }, true)).toBe(false);
    expect(sendsOnKey({ key: "Enter", shiftKey: false, isComposing: false }, false)).toBe(true);
  });

  it("sends nothing blank and at most 1000 characters", () => {
    expect(cleanQuestion("   \n ")).toBeNull();
    expect(cleanQuestion("  total?  ")).toBe("total?");
    expect(cleanQuestion("x".repeat(1200))?.length).toBe(1000);
  });

  it("makes a down vote's reason optional and short", () => {
    expect(cleanReason("  ")).toBeUndefined();
    expect(cleanReason(" wrong page ")).toBe("wrong page");
    expect(cleanReason("y".repeat(600))?.length).toBe(500);
  });
});

describe("question box while a question is sent", () => {
  const typed = "  Total of invoice 12?\n";

  it("clears on send, and gets the question back, as typed, if sending fails", () => {
    expect(cleanQuestion(typed)).toBe("Total of invoice 12?");
    expect(restoreAfterFailure("", typed)).toBe(typed);
  });

  it("never overwrites what was typed while the answer loaded", () => {
    expect(restoreAfterFailure("And invoice 13?", typed)).toBe("And invoice 13?");
    expect(restoreAfterFailure(" ", typed)).toBe(" ");
  });

  it("refuses a whitespace-only question, so the box is not cleared", () => {
    expect(cleanQuestion("  \n\t ")).toBeNull();
    expect(cleanQuestion("")).toBeNull();
  });
});

describe("what the chat status line says", () => {
  const answer = (overrides: Partial<Answer> = {}): Answer => ({
    status: "answered",
    label: "verified",
    sentences: [
      { text: "The total is 1,234.", citations: [1] },
      { text: "It was paid in March.", citations: [1] },
    ],
    citations: [citation(1)],
    closest_documents: [],
    route: "fixed_query",
    normalized_question: "total",
    model_used: false,
    ...overrides,
  });

  it("gives the label and the first sentence only", () => {
    expect(answerStatusText(answer())).toBe("Answer ready. Verified. The total is 1,234.");
    expect(answerStatusText(answer({ label: "needs_review" }))).toBe(
      "Answer ready. Needs review. The total is 1,234.",
    );
  });

  it("never says Verified for an unchecked answer or a missing label", () => {
    expect(answerStatusText(answer({ status: "unchecked" }))).toMatch(
      /^Answer ready\. Needs review\. /,
    );
    expect(answerStatusText(answer({ label: null }))).toMatch(/^Answer ready\. Needs review\. /);
  });

  it("says plainly when nothing was found", () => {
    const none = answer({ status: "not_found", label: null, sentences: [], citations: [] });
    expect(answerStatusText(none)).toBe("No answer found in your documents.");
    expect(answerStatusText(null)).toBe("No answer found in your documents.");
  });
});

describe("pages that couldn't be read (PRD 5 F6, U7)", () => {
  const NOTE = "Not found in the readable text. 2 pages to check: Invoice_12.pdf page 3, Register.jpg page 1.";
  const notFound: Answer = {
    status: "not_found",
    label: null,
    sentences: [],
    citations: [],
    closest_documents: [],
    route: "search",
    normalized_question: "total",
    model_used: false,
    reading_note: NOTE,
    unreadable_pages: [
      { document_id: "d1", document_name: "Invoice_12.pdf", page_no: 3, read_quality: "unreadable" },
      { document_id: "d2", document_name: "Register.jpg", page_no: 1, read_quality: "uncertain" },
    ],
    unreadable_page_count: 3,
  };

  it("gives the server's note with a link to each page it names, and how many more", () => {
    const note = readingNote(notFound);
    expect(note?.text).toBe(NOTE);
    expect(note?.pages).toEqual([
      { href: "/documents/d1?page=3", text: "Invoice_12.pdf page 3" },
      { href: "/documents/d2?page=1", text: "Register.jpg page 1" },
    ]);
    expect(note?.more).toBe(1);
  });

  it("has no note for an answer without one, or from before it", () => {
    expect(readingNote({ reading_note: null })).toBeNull();
    expect(readingNote({})).toBeNull();
    expect(readingNote({ reading_note: HARD_TO_READ_TEXT })).toEqual({ text: HARD_TO_READ_TEXT, pages: [], more: 0 });
  });

  it("shows the note and the page links under a not-found answer", () => {
    const message: Message = {
      id: "a1",
      role: "assistant",
      text: "",
      created_at: "2026-10-09T10:00:00Z",
      answer: notFound,
      feedback: null,
    };
    const html = renderToStaticMarkup(createElement(AnswerView, { message, question: "total?" }));
    expect(html).toContain("find this in your documents.");
    expect(html).toContain("Not found in the readable text.");
    expect(html).toContain('href="/documents/d1?page=3"');
    expect(html).toContain("and 1 more");
  });

  it("says a source card's text was hard to read, and nothing when it was not", () => {
    const hard = renderToStaticMarkup(
      createElement(SourceCard, { citation: citation(1, { hard_to_read: true }), messageId: "a1" }),
    );
    expect(hard).toContain(HARD_TO_READ_TEXT);
    const clear = renderToStaticMarkup(createElement(SourceCard, { citation: citation(1), messageId: "a1" }));
    expect(clear).not.toContain(HARD_TO_READ_TEXT);
  });
});

describe("a chip lights its source card", () => {
  it("marks the card hot only when its chip is pointed at or focused", () => {
    const hot = renderToStaticMarkup(createElement(SourceCard, { citation: citation(2), messageId: "a1", hot: true }));
    expect(hot).toMatch(/<article[^>]*data-hot="true"/);
    const cold = renderToStaticMarkup(createElement(SourceCard, { citation: citation(2), messageId: "a1" }));
    expect(cold).not.toContain("data-hot");
    const gone = renderToStaticMarkup(
      createElement(SourceCard, { citation: citation(2, { source_state: "deleted" }), messageId: "a1", hot: true }),
    );
    expect(gone).toMatch(/<article[^>]*data-hot="true"/);
  });

  it("wires each chip to its own card, and styles the hot card", () => {
    const view = readFileSync(join(__dirname, "../components/chat/AnswerView.tsx"), "utf8");
    for (const handler of [
      "onPointerEnter={() => setHot(n)}",
      "onFocus={() => setHot(n)}",
      "onPointerLeave={() => setHot(null)}",
      "onBlur={() => setHot(null)}",
    ]) {
      expect(view).toContain(handler);
    }
    expect(view).toContain("hot={hot === citation.n}");
    const css = readFileSync(join(__dirname, "../components/chat/Chat.module.css"), "utf8");
    expect(css).toMatch(/\.source\[data-hot\] \{\s*border-color: var\(--accent\);\s*box-shadow: 0 0 0 1px var\(--accent\);\s*\}/);
  });
});
