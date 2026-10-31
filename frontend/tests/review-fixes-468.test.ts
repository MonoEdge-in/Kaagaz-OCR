import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ASK_DRAFT, askDraft } from "@/components/chat/logic";
import { endedPath, pathAfterSignIn, SEARCH_DRAFT, searchToKeep } from "@/components/shell/gate";
import { clearDrafts, readDraft, saveDraft } from "@/lib/drafts";

// The #468 council review's fixes. Each describe names its item.

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

/** A tab's sessionStorage, in memory. */
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
    this.items.set(key, String(value));
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
}

let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  vi.stubGlobal("sessionStorage", storage);
});

afterEach(() => {
  clearDrafts();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("SEC1: ?next= never carries one person's words to the next", () => {
  it("endedPath puts the path only in next, never a query", () => {
    expect(endedPath("/search?q=salary%20of%20Mehta&in=names")).toBe("/login?next=%2Fsearch&ended=1");
    expect(endedPath("/chat?q=What%20did%20I%20pay&send=1")).toBe("/login?next=%2Fchat&ended=1");
    expect(endedPath("/documents/d-1#page-2")).toBe("/login?next=%2Fdocuments%2Fd-1&ended=1");
    for (const place of ["/search?q=secret", "/chat?q=secret", "/admin?tab=secret"]) {
      const next = new URLSearchParams(endedPath(place).split("?")[1]).get("next") ?? "";
      expect(next, place).not.toMatch(/[?#]|secret/);
      expect(pathAfterSignIn({ next })).not.toContain("secret");
    }
  });

  it("the session's end is given the pathname only", () => {
    const session = read("lib/session.tsx");
    expect(session).toContain("leavePage(endedPath(window.location.pathname))");
    expect(session).not.toContain("window.location.pathname + window.location.search");
  });

  it("a search's words are kept as a draft of the person whose session ended", () => {
    expect(searchToKeep("/search", "?q=rent%20paid&in=names")).toEqual({ q: "rent paid" });
    expect(searchToKeep("/search", "?q=%20%20")).toBeNull();
    expect(searchToKeep("/search", "")).toBeNull();
    expect(searchToKeep("/documents", "?q=rent")).toBeNull();
    const session = read("lib/session.tsx");
    expect(session).toMatch(/saveDraft\(lastUserId\.current, SEARCH_DRAFT, kept\)/);
  });

  it("the same person gets their search back on /search; nobody else does", () => {
    saveDraft(A, SEARCH_DRAFT, { q: "rent paid" });
    expect(readDraft(A, SEARCH_DRAFT)).toEqual({ q: "rent paid" });
    expect(readDraft(B, SEARCH_DRAFT)).toBeNull();
    const screen = read("components/search/SearchScreen.tsx");
    expect(screen).toContain("readDraft(userId, SEARCH_DRAFT)");
    expect(screen).toMatch(/if \(q === "" && typeof kept === "string" && kept\.trim\(\)\) router\.replace\(searchHref\(kept, mode\)\)/);
  });

  it("Ask's ?q= words live in the user-keyed Ask draft, which another person never reads", () => {
    // ChatScreen puts ?q= in the box (draft), and the box tells onTyped, which keeps ASK_DRAFT.
    const screen = read("components/chat/ChatScreen.tsx");
    expect(screen).toContain(": (questionFromSearch(initialSearch) ?? undefined)");
    expect(read("components/chat/QuestionBox.tsx")).toMatch(/useEffect\(\(\) => \{\s*onTyped\?\.\(text\);/);
    saveDraft(A, ASK_DRAFT, askDraft("What did I pay Mehta?", null));
    expect(readDraft(A, ASK_DRAFT)).toEqual({ typed: "What did I pay Mehta?" });
    expect(readDraft(B, ASK_DRAFT)).toBeNull();
  });
});
