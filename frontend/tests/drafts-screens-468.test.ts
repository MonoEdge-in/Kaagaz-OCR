import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ASK_DRAFT, askDraft, askDraftText, settleWaitingDraft } from "@/components/chat/logic";
import { clearDrafts, readDraft, saveDraft, saveDraftSoon } from "@/lib/drafts";

// Issue 468 (A1): the screens that keep unsent work as a draft (lib/drafts.ts): Ask's box (the
// typing and the question still waiting), a Review card's typed value, and a summary fact's edit.

const A = "11111111-1111-4111-8111-111111111111";
const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

class MemoryStorage {
  items = new Map<string, string>();
  get length() {
    return this.items.size;
  }
  key(index: number) {
    return [...this.items.keys()][index] ?? null;
  }
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.items.set(key, value);
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
}

beforeEach(() => {
  vi.stubGlobal("sessionStorage", new MemoryStorage());
});

afterEach(() => {
  clearDrafts();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Ask's draft (components/chat/logic.ts)", () => {
  it("keeps the typing and the question waiting, and nothing when both are empty", () => {
    expect(askDraft("What is the total?", null)).toEqual({ typed: "What is the total?" });
    expect(askDraft("", "Who paid?")).toEqual({ waiting: "Who paid?" });
    expect(askDraft("next one", "Who paid?")).toEqual({ typed: "next one", waiting: "Who paid?" });
    expect(askDraft("   ", null)).toBeNull();
    expect(askDraft("", null)).toBeNull();
  });

  it("puts back the question that got no answer first, else the typing", () => {
    expect(askDraftText({ typed: "next one", waiting: "Who paid?" })).toBe("Who paid?");
    expect(askDraftText({ typed: "next one" })).toBe("next one");
    expect(askDraftText({ typed: "  " })).toBeNull();
    expect(askDraftText({ waiting: 3 })).toBeNull();
    expect(askDraftText(null)).toBeNull();
  });

  it("a question answered after its screen was gone is no longer waiting", () => {
    saveDraft(A, ASK_DRAFT, askDraft("", "Who paid?"));
    settleWaitingDraft(A, "Who paid?", true);
    expect(readDraft(A, ASK_DRAFT)).toBeNull();
  });

  it("a question that failed after its screen was gone stays, as typing", () => {
    saveDraft(A, ASK_DRAFT, askDraft("", "Who paid?"));
    settleWaitingDraft(A, "Who paid?", false);
    expect(readDraft(A, ASK_DRAFT)).toEqual({ typed: "Who paid?" });
  });

  it("leaves a newer draft alone, and writes a save still waiting first", () => {
    vi.useFakeTimers();
    saveDraft(A, ASK_DRAFT, askDraft("", "Newer question"));
    settleWaitingDraft(A, "Older question", true);
    expect(readDraft(A, ASK_DRAFT)).toEqual({ waiting: "Newer question" });
    saveDraftSoon(A, ASK_DRAFT, askDraft("typed meanwhile", "Newer question"));
    settleWaitingDraft(A, "Newer question", true);
    expect(readDraft(A, ASK_DRAFT)).toEqual({ typed: "typed meanwhile" });
  });

  it("is wired into the chat screen: read once in the browser, never sent by itself", () => {
    const screen = read("components/chat/ChatScreen.tsx");
    expect(screen).toMatch(/if \(hydrated && !draftRead\) \{/);
    // Only when nothing else fills the box: a picked-up question or a ?q= link wins.
    // Changed on purpose (#468 review, UX4): the restored words also show the kept-draft line.
    expect(screen).toContain("if (kept !== null && draft === undefined && resumed === null && autoQuestion === null) {");
    // The kept words only fill the box (setDraft): no onAsk with them.
    expect(screen).not.toMatch(/onAsk\(kept/);
    // Sending keeps the question as waiting at once; settled, it is no longer waiting.
    expect(screen).toMatch(/waitingNow\.current = question;\s*saveDraft\(userId, ASK_DRAFT, askDraft\(typedNow\.current, question\)\);/);
    expect(screen).toMatch(/waitingNow\.current = null;\s*if \(!sent\) typedNow\.current = restoreAfterFailure\(typedNow\.current, question\);\s*keepDraft\(\);/);
    expect(screen).toContain("settleWaitingDraft(userId, question, sent);");
    expect(screen).toContain("onTyped={onTyped}");
    expect(read("components/chat/QuestionBox.tsx")).toMatch(/useEffect\(\(\) => \{\s*onTyped\?\.\(text\);\s*\}, \[text, onTyped\]\);/);
  });
});

describe("Review and summary edits keep a draft", () => {
  it("a Review card reads its draft, keeps the typed value (never Remember), and clears it once settled", () => {
    const card = read("components/review/ReviewCard.tsx");
    expect(card).toContain("readDraft(userId, draftKey)?.typed");
    expect(card).toContain("saveDraftSoon(userId, draftKey, typed.trim() ? { typed } : null);");
    expect(card).toContain("const [remember, setRemember] = useState(false);");
    expect(card).toMatch(/if \(done\.ok\) \{\s*clearDraft\(userId, draftKey\);/);
  });

  it("a summary fact's edit opens again from its draft without taking focus, and clears on Save or Cancel", () => {
    const card = read("app/(app)/documents/[id]/SummaryCard.tsx");
    expect(card).toContain('readDraft(userId, draftKey)');
    expect(card).toContain("const [editing, setEditing] = useState(kept !== null);");
    expect(card).toMatch(/if \(editing && !reopened\.current\) field\.current\?\.focus\(\);/);
    expect(card).toContain("saveDraftSoon(userId, draftKey, editing ? { typed } : null);");
    expect(card.match(/clearDraft\(userId, draftKey\)/g)).toHaveLength(2);
  });
});
