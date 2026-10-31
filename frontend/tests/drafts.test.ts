import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearDraft,
  clearDrafts,
  flushDrafts,
  isSecretField,
  keepDraftsOf,
  readDraft,
  saveDraft,
  saveDraftSoon,
} from "@/lib/drafts";

// Issue 468: unsent work is kept in this tab when a session ends, for the same person only.

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";

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
  clear() {
    this.items.clear();
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

describe("drafts (lib/drafts.ts)", () => {
  it("are restored for the same user, on the same screen only", () => {
    saveDraft(A, "ask", { question: "What is the total?" });
    expect(readDraft(A, "ask")).toEqual({ question: "What is the total?" });
    expect(readDraft(A, "summary")).toBeNull();
  });

  // Changed on purpose (#468 review, PT5): reading never shows another user's draft (the key is the
  // user's own), and no longer drops it; keepDraftsOf does that when someone signs in.
  it("are never shown to another user", () => {
    saveDraft(A, "ask", { question: "A's question" });
    expect(readDraft(B, "ask")).toBeNull();
    expect([...storage.items.keys()]).toEqual([`kaagaz-draft:${A}:ask`]);
  });

  // Changed on purpose (#468 review, PT5): the owner re-check inside the value is gone; only
  // saveDraft writes these keys, always under the user's own id.
  it("are read under the user's own key only", () => {
    storage.setItem(`kaagaz-draft:${A}:ask`, JSON.stringify({ userId: A, fields: { question: "A's" } }));
    expect(readDraft(A, "ask")).toEqual({ question: "A's" });
    expect(readDraft(B, "ask")).toBeNull();
  });

  it("keepDraftsOf drops everyone else's drafts, waiting ones included", () => {
    vi.useFakeTimers();
    saveDraft(A, "ask", { question: "mine" });
    saveDraft(B, "ask", { question: "theirs" });
    saveDraftSoon(B, "summary", { text: "theirs, waiting" });
    keepDraftsOf(A);
    vi.runAllTimers();
    expect([...storage.items.keys()]).toEqual([`kaagaz-draft:${A}:ask`]);
  });

  it("never contain a password, code, token or file", () => {
    saveDraft(A, "add-person", {
      name: "Asha",
      email: "asha@example.com",
      role: "member",
      password: "x",
      temporaryPassword: "x",
      temp_password: "x",
      "new-password": "x",
      code: "123456",
      totpCode: "123456",
      token: "t",
      resetToken: "t",
      secret: "s",
      file: "f",
      files: ["f"],
      nested: { password: "x", kept: "yes" },
    });
    const raw = storage.getItem(`kaagaz-draft:${A}:add-person`) ?? "";
    expect(raw).not.toMatch(/password|123456|"t"|"x"|"s"|"f"/i);
    expect(readDraft(A, "add-person")).toEqual({
      name: "Asha",
      email: "asha@example.com",
      role: "member",
      nested: { kept: "yes" },
    });
  });

  it("never contain a file object, only plain values", () => {
    const blob = new Blob(["secret"]);
    saveDraft(A, "upload", { note: "kept", attachment: blob as never, list: ["a", blob as never], when: new Date() as never });
    expect(readDraft(A, "upload")).toEqual({ note: "kept", list: ["a"] });
  });

  it("keep settings fields that only look like secret words inside a longer word (autofile)", () => {
    expect(isSecretField("autofile")).toBe(false);
    expect(isSecretField("autofileOn")).toBe(false);
    expect(isSecretField("adminPassword")).toBe(true);
    saveDraft(A, "ai-settings", { autofile: { level: "high", locked: false } });
    expect(readDraft(A, "ai-settings")).toEqual({ autofile: { level: "high", locked: false } });
  });

  it("are cleared on sign-out (clearDrafts), waiting ones included", () => {
    vi.useFakeTimers();
    saveDraft(A, "ask", { question: "q" });
    saveDraftSoon(A, "summary", { text: "s" });
    clearDrafts();
    vi.runAllTimers();
    expect(storage.items.size).toBe(0);
    expect(readDraft(A, "ask")).toBeNull();
  });

  it("are cleared once sent, and a save still waiting does not bring it back", () => {
    vi.useFakeTimers();
    saveDraftSoon(A, "review", { value: "12.50" });
    clearDraft(A, "review");
    vi.runAllTimers();
    expect(readDraft(A, "review")).toBeNull();
  });

  it("an empty or null draft removes the kept one", () => {
    saveDraft(A, "ask", { question: "q" });
    saveDraft(A, "ask", null);
    expect(readDraft(A, "ask")).toBeNull();
    saveDraft(A, "ask", { question: "q" });
    saveDraft(A, "ask", { password: "only a secret" });
    expect(readDraft(A, "ask")).toBeNull();
  });

  it("saveDraftSoon waits, keeps only the latest, and flushDrafts writes it now", () => {
    vi.useFakeTimers();
    saveDraftSoon(A, "summary", { text: "fir" });
    saveDraftSoon(A, "summary", { text: "first draft" });
    expect(storage.items.size).toBe(0);
    flushDrafts();
    // Changed on purpose (#468 review, CR5): the storage is checked, as readDraft writes a waiting save first.
    expect(storage.items.size).toBe(1);
    expect(readDraft(A, "summary")).toEqual({ text: "first draft" });
    saveDraftSoon(A, "summary", { text: "second" });
    vi.advanceTimersByTime(600);
    expect(readDraft(A, "summary")).toEqual({ text: "second" });
  });

  it("a stored value that is not a draft reads as none", () => {
    storage.setItem(`kaagaz-draft:${A}:ask`, "not json");
    expect(readDraft(A, "ask")).toBeNull();
    storage.setItem(`kaagaz-draft:${A}:ask`, JSON.stringify({ userId: A, fields: ["a"] }));
    expect(readDraft(A, "ask")).toBeNull();
  });

  it("storage that throws breaks nothing: no draft, no error", () => {
    const refuse = () => {
      throw new DOMException("refused", "SecurityError");
    };
    vi.stubGlobal("sessionStorage", {
      get length() {
        return refuse();
      },
      key: refuse,
      getItem: refuse,
      setItem: refuse,
      removeItem: refuse,
      clear: refuse,
    });
    vi.useFakeTimers();
    expect(() => saveDraft(A, "ask", { question: "q" })).not.toThrow();
    expect(() => saveDraftSoon(A, "ask", { question: "q" })).not.toThrow();
    expect(() => flushDrafts()).not.toThrow();
    expect(() => vi.runAllTimers()).not.toThrow();
    expect(readDraft(A, "ask")).toBeNull();
    expect(() => clearDraft(A, "ask")).not.toThrow();
    expect(() => keepDraftsOf(A)).not.toThrow();
    expect(() => clearDrafts()).not.toThrow();
  });

  it("no storage at all (the server) breaks nothing", () => {
    vi.stubGlobal("sessionStorage", undefined);
    expect(() => saveDraft(A, "ask", { question: "q" })).not.toThrow();
    expect(readDraft(A, "ask")).toBeNull();
    expect(() => clearDrafts()).not.toThrow();
  });
});
