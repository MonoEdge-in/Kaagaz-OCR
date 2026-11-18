// Tushar's UI audit (16 Oct 2026), the Ask, My words, Review and Admin items: each guard
// renders in node or reads the source, so the old habit coming back fails.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement, createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RowMenu, menuKeyTarget } from "@/components/admin/RowMenu";
import { activeDays, averageText } from "@/components/admin/UsagePanel";
import { AnswerView } from "@/components/chat/AnswerView";
import { ConversationList } from "@/components/chat/ConversationList";
import { filterConversations, starterQuestions } from "@/components/chat/logic";
import { QuestionBox } from "@/components/chat/QuestionBox";
import {
  QUEUE_VIEWS,
  focusIndexAfter,
  focusKeyAction,
  focusProgress,
  reasonTag,
} from "@/components/review/review";
import { draftFilled, wordsCsv } from "@/components/words/words";
import { MODE_TEXTS } from "@/lib/chatMode";
import type { DocumentSummary, Message, ReviewItem, ReviewList } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("Review (R2 to R6)", () => {
  // Changed on purpose (Tushar, 16 Oct 2026: Ready to verify): the words, and the badge is a Tip
  // with the value's confidence.
  it("R2: says why a value is ready to verify instead of repeating Ready to verify", () => {
    expect(reasonTag("readers_disagree")).toBe("Readers disagree");
    expect(reasonTag("low_confidence")).toBe("Low confidence");
    expect(reasonTag("direct_cell")).toBe("Ready to verify"); // a reason with no short words
    const card = read("components/review/ReviewCard.tsx");
    expect(card).not.toContain("<LabelBadge");
    expect(card).toMatch(
      /<Tip className="badge badge-review" text=\{confidenceTip\(value\.confidence \?\? null\)\}>\s*<Icon name="eye-alert"[^>]*\/>\s*<span className="visually-hidden">Ready to verify: <\/span>\s*\{reasonTag\(value\.label_reason\)\}/,
    );
  });

  it("R3: maps the keys 1, 2, E, S and Enter, never while typing or with a modifier", () => {
    expect(focusKeyAction("1", "other", false)).toBe("first");
    expect(focusKeyAction("2", "control", false)).toBe("second");
    expect(focusKeyAction("e", "other", false)).toBe("edit");
    expect(focusKeyAction("S", "other", false)).toBe("skip");
    expect(focusKeyAction("Enter", "other", false)).toBe("save");
    expect(focusKeyAction("Enter", "control", false)).toBeNull(); // the button or link is pressed instead
    expect(focusKeyAction("1", "typing", false)).toBeNull();
    expect(focusKeyAction("s", "other", true)).toBeNull();
    expect(focusKeyAction("x", "other", false)).toBeNull();
  });

  it("R3: counts '3 of 15' with the values settled in this visit", () => {
    expect(focusProgress(0, 0, 15)).toBe("1 of 15");
    expect(focusProgress(2, 0, 13)).toBe("3 of 15");
    expect(focusProgress(1, 1, 14)).toBe("3 of 15"); // one settled, one skipped
  });

  it("R3: moves on to the same place, loads more there, or goes back to the first left", () => {
    const list = (shown: number, total: number): ReviewList => ({
      items: Array.from({ length: shown }, (_, n) => ({ value: { id: `v${n}` } }) as unknown as ReviewItem),
      total,
    });
    expect(focusIndexAfter(list(3, 3), 1)).toBe(1);
    expect(focusIndexAfter(list(3, 9), 3)).toBe("more");
    expect(focusIndexAfter(list(3, 3), 3)).toBe(0);
    expect(focusIndexAfter(list(0, 0), 0)).toBeNull();
  });

  // Changed on purpose (PRD 9 F-17): one card at a time is the default for one document's or one
  // reason's values; Review itself opens on By document.
  it("R3: shows one card at a time for one document's or one reason's values, with Skip and the keys on it", () => {
    const queue = read("components/review/ReviewQueue.tsx");
    // Changed on purpose (PRD 9 F-17): Review opens on By document; one document's or one reason's
    // values (from the address) still open one at a time.
    expect(queue).toContain('useState<QueueView>(documentId || initialReason ? "focus" : "document")');
    expect(queue).toContain("<ReviewCard key={shown.value.id} item={shown} onResolved={onResolved} onSkip={skip} />");
    const card = read("components/review/ReviewCard.tsx");
    expect(card).toContain("onKeyDown={onSkip ? onKeyDown : undefined}");
    expect(card).toMatch(/aria-keyshortcuts="S" onClick=\{onSkip\}>\s*Skip/);
  });

  it("R4: leaves Remember this correction unticked until the reviewer ticks it", () => {
    expect(read("components/review/ReviewCard.tsx")).toContain("const [remember, setRemember] = useState(false);");
  });

  it("R5: says No second reading in one muted line, outside the readings", () => {
    expect(read("components/review/ReviewCard.tsx")).toMatch(
      /\{value\.second_reading === null \? \(\s*<p className=\{`muted \$\{styles\.noReading\}`\}>No second reading for this value\.<\/p>/,
    );
  });

  it("R6: picks the view with one segmented control of native radios", () => {
    // Changed on purpose (PRD 9 F-17): By document first, the view Review opens on.
    expect(QUEUE_VIEWS.map((view) => view.text)).toEqual(["By document", "One at a time", "All items"]);
    const queue = read("components/review/ReviewQueue.tsx");
    expect(queue).toContain('role="radiogroup" aria-label="Show"');
    expect(queue).not.toContain('type="checkbox"');
  });
});

const conversation = (id: string, title: string) => ({ id, title, created_at: "2026-10-16T05:00:00Z" });

describe("Ask (A1 to A6)", () => {
  it("A1: finds conversations by every word of the search, in any case", () => {
    const items = [conversation("a", "GST bills for September"), conversation("b", "Rent register")];
    expect(filterConversations(items, "  sept GST ").map((item) => item.id)).toEqual(["a"]);
    expect(filterConversations(items, "")).toHaveLength(2);
    expect(filterConversations(items, "salary")).toEqual([]);
  });

  it("A1 and A6: lists a search, each conversation's menu, and a Refresh with its name and tooltip", () => {
    const html = renderToStaticMarkup(
      createElement(ConversationList, {
        items: [conversation("a", "GST bills")],
        failed: false,
        activeId: null,
        disabled: false,
        onOpen: () => undefined,
        onNew: () => undefined,
        onRetry: () => undefined,
        onRename: async () => true,
        onDelete: async () => true,
      }),
    );
    // Changed on purpose (Tushar, 17 Oct 2026, Ask screen polish): a lens and a placeholder in the box.
    expect(html).toMatch(
      /<label for="[^"]+-search">Search conversations<\/label><div[^>]*><svg class="[^"]*searchIcon[^"]*"[\s\S]*?<\/svg><input id="[^"]+-search" type="search"[^>]*placeholder="Search conversations"/,
    );
    expect(html).not.toContain('class="field'); // the global field label is all caps
    expect(html).toContain('aria-label="Actions for GST bills"');
    // Changed on purpose (plain words, 25 Oct 2026): each item has a line under it saying what it does.
    expect(html).toMatch(
      /role="menuitem"[^>]*>Rename<span[^>]*>Give this conversation a new name\.<\/span><\/button><button[^>]*role="menuitem"[^>]*>Delete<span[^>]*>[^<]+<\/span><\/button>/,
    );
    expect(html).toMatch(/aria-label="Refresh conversations" title="Refresh conversations"/);
  });

  it("Ask screen polish: one line per conversation, its time beside the title, an icon-only menu", () => {
    const html = renderToStaticMarkup(
      createElement(ConversationList, {
        items: [conversation("a", "GST bills")],
        failed: false,
        activeId: "a",
        disabled: false,
        onOpen: () => undefined,
        onNew: () => undefined,
        onRetry: () => undefined,
        onRename: async () => true,
        onDelete: async () => true,
      }),
    );
    expect(html).toMatch(
      /<button type="button" class="[^"]*item[^"]*" aria-current="true"><span class="[^"]*itemTitle[^"]*" title="GST bills">GST bills<\/span><time class="[^"]*itemTime[^"]*"/,
    );
    expect(html).toMatch(/<button popoverTarget="[^"]+" aria-haspopup="menu"[^>]*aria-label="More actions for GST bills"[^>]*><svg[\s\S]*?<\/svg><\/button>/);
    expect(html).not.toMatch(/>More</);
    const css = read("components/chat/Chat.module.css");
    expect(css).toMatch(/\.itemTitle \{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/);
    expect(css).toMatch(/\.itemTime \{[^}]*font-variant-numeric: tabular-nums;/);
    expect(css).toMatch(/\.itemRow > \[aria-haspopup="menu"\] \{[^}]*opacity: 0;/);
    expect(css).toMatch(/\.itemRow:focus-within > \[aria-haspopup="menu"\]/);
    expect(css).toMatch(/@media \(pointer: coarse\) \{\s*\.itemRow > \[aria-haspopup="menu"\] \{\s*position: static;\s*opacity: 1;/);
    expect(css).toMatch(/\.pane \{[^}]*overscroll-behavior: contain;[^}]*scrollbar-width: thin;/);
  });

  it("A2 and A3: one toolbar row, and Send dimmed while the box is empty", () => {
    const html = renderToStaticMarkup(
      createElement(QuestionBox, {
        pending: false,
        error: null,
        onAsk: async () => true,
        inputRef: createRef<HTMLTextAreaElement>(),
        texts: MODE_TEXTS.documents,
      }),
    );
    expect(html).toMatch(/<button type="button" class="btn btn-primary [^"]*" aria-disabled="true"><svg[\s\S]*?<span>Send question<\/span><\/button>/);
    const css = read("components/chat/Chat.module.css");
    expect(css).toMatch(/\.toolbar \{\s*display: flex;\s*flex-wrap: nowrap;/);
    expect(css).toMatch(/\.composerBox:has\(\.composerInput:focus\) \{[^}]*outline: 2px solid var\(--focus\);\s*outline-offset: 2px;/);
  });

  const notFound: Message = {
    id: "a1",
    role: "assistant",
    text: "",
    created_at: "2026-10-16T05:00:00Z",
    feedback: null,
    answer: {
      status: "not_found",
      label: null,
      sentences: [],
      citations: [],
      closest_documents: [],
      route: "search",
      normalized_question: "rent",
      model_used: false,
      reading_note: null,
    } as unknown as Message["answer"],
  };

  it("A4: ends a not-found answer with calm next steps, never 'couldn't'", () => {
    const plain = renderToStaticMarkup(createElement(AnswerView, { message: notFound, question: "rent?" }));
    expect(plain).toContain("Next steps");
    expect(plain).toContain('<a href="/words">Add it to My words</a>');
    expect(plain).toContain('<a href="/documents">Upload the document</a>');
    expect(plain).not.toMatch(/couldn.t|can.t find/i);
    expect(plain).not.toContain("Search all documents");
    const wider = renderToStaticMarkup(
      createElement(AnswerView, { message: notFound, question: "rent?", onAskAll: () => undefined }),
    );
    expect(wider).toContain("Search all documents");
  });

  it("A5: suggests up to three questions about the newest ready documents, by their type", () => {
    const doc = (id: string, changes: Partial<DocumentSummary>) =>
      ({ id, display_name: `${id}.pdf`, status: "ready", deleted_at: null, doc_type: null, ...changes }) as DocumentSummary;
    const starters = starterQuestions([
      doc("bill", { doc_type: "invoice" }),
      doc("scan", { status: "clean" }),
      doc("bank", { doc_type: "bank_statement" }),
      doc("note", {}),
      doc("more", { doc_type: "receipt" }),
    ]);
    expect(starters).toEqual([
      { documentId: "bill", documentName: "bill.pdf", question: "What is the total amount on this invoice?" },
      { documentId: "bank", documentName: "bank.pdf", question: "What is the closing balance?" },
      { documentId: "note", documentName: "note.pdf", question: "What is this document about?" },
    ]);
  });

  it("Ask screen polish: never suggests the same question twice, and varies them", () => {
    const doc = (id: string, changes: Partial<DocumentSummary> = {}) =>
      ({ id, display_name: id, status: "ready", deleted_at: null, doc_type: null, ...changes }) as DocumentSummary;
    const untyped = starterQuestions([doc("JCS-051.pdf"), doc("IMG_0204.jpeg"), doc("scan.pdf", { doc_type: "other" })]);
    expect(untyped.map((starter) => starter.question)).toEqual([
      "What is this document about?",
      "Who are the parties named in it?",
      "What amounts does it mention?",
    ]);
    const bills = starterQuestions([doc("a", { doc_type: "invoice" }), doc("b", { doc_type: "invoice" }), doc("c")]);
    expect(bills.map((starter) => starter.question)).toEqual([
      "What is the total amount on this invoice?",
      "What is this document about?",
      "Who are the parties named in it?",
    ]);
    expect(starterQuestions(Array.from({ length: 6 }, (_, i) => doc(`d${i}`)))).toHaveLength(3);
    const css = read("components/chat/Chat.module.css");
    expect(css).toMatch(/\.starterList \{[^}]*grid-template-columns: repeat\(auto-fit, minmax\(min\(100%, 240px\), 1fr\)\);/);
    expect(css).toMatch(/\.empty > :first-child \{\s*padding-block: 0;/); // one block, no dead gap
  });
});

describe("My words (W1, W2)", () => {
  it("W1: Add is ready only with both boxes filled", () => {
    expect(draftFilled({ word: "malak", meaning: "owner" })).toBe(true);
    expect(draftFilled({ word: "malak", meaning: "  " })).toBe(false);
    expect(draftFilled({ word: "", meaning: "owner" })).toBe(false);
    expect(read("components/words/MyWordsScreen.tsx")).toContain("disabled={!draftFilled(draft)}");
  });

  it("W2: saves the words as CSV, quoting commas and keeping spreadsheets from running a formula", () => {
    expect(wordsCsv([{ word: "malak", meaning: "owner" }, { word: "-bhada", meaning: "rent, fare" }])).toBe(
      'word,meaning\nmalak,owner\n\'-bhada,"rent, fare"\n',
    );
  });
});

describe("Admin (AD2, AD5)", () => {
  it("AD2: moves through a row menu with Up, Down, Home and End", () => {
    expect(menuKeyTarget("ArrowDown", 2, 3)).toBe(0);
    expect(menuKeyTarget("ArrowUp", 0, 3)).toBe(2);
    expect(menuKeyTarget("End", 0, 3)).toBe(2);
    expect(menuKeyTarget("Home", 2, 3)).toBe(0);
    expect(menuKeyTarget("a", 0, 3)).toBeNull();
  });

  it("AD2: names whose actions they are, and says why one can't be used yet", () => {
    const html = renderToStaticMarkup(
      createElement(RowMenu, {
        name: "Ravi",
        items: [
          { label: "Allow Delete forever", onSelect: () => undefined, disabledReason: "Turn on your two-step sign-in first." },
          { label: "Disable", onSelect: () => undefined, destructive: true },
        ],
      }),
    );
    expect(html).toContain('<span class="visually-hidden"> actions for Ravi</span>');
    expect(html).toMatch(/aria-disabled="true">Allow Delete forever<span[^>]*>Turn on your two-step sign-in first\.<\/span>/);
    expect(html.indexOf("Disable")).toBeGreaterThan(html.indexOf("Allow Delete forever"));
  });

  it("AD5: averages over the days with use, never money", () => {
    const report = { by_day: [{ day: "2026-10-01", input_tokens: 100, output_tokens: 50 }, { day: "2026-10-02", input_tokens: 0, output_tokens: 0 }] };
    expect(activeDays(report as never)).toBe(1);
    expect(averageText(150000, 1, 11)).toBe("Average per active day: 150K (1 of 11 days)");
  });
});
