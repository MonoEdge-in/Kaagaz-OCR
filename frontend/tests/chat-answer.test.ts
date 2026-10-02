import { describe, expect, it } from "vitest";
import {
  answerLabelText,
  cardId,
  citationHighlights,
  citationLink,
  citationMap,
  citationRegion,
  citationSource,
  cleanQuestion,
  cleanReason,
  NOT_FOUND_TEXT,
  questionReading,
  questionsByAnswer,
  sendsOnKey,
  sentenceCitations,
  understoodAs,
} from "@/components/chat/answer";
import { decodeRegion } from "@/lib/regions";
import type { Citation, Message, Region } from "@/lib/types";

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
