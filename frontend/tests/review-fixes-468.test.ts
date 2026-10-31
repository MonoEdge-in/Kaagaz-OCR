import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { aiChangesToKeep, orgDraftOver, settingsDraft, settingsFromDraft } from "@/components/admin/logic";
import { ASK_DRAFT, askDraft } from "@/components/chat/logic";
import { endedPath, pathAfterSignIn, SEARCH_DRAFT, searchToKeep } from "@/components/shell/gate";
import { ApiError, GET_TIMEOUT_MS, TOO_SLOW, getDocument } from "@/lib/api";
import { changedOnly, orgDraftOf } from "@/lib/aiSettings";
import { clearDrafts, readDraft, saveDraft } from "@/lib/drafts";
import type { AiSettings, AiTaskSetting, CustomerSettings, TaskLevel } from "@/lib/types";

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
  vi.restoreAllMocks();
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

describe("SF1 / CR4: a failed document refresh is silent only while the browser is offline (structural)", () => {
  it("keeps the old page only when offline; a timeout or no internet while online shows the error with Try again", () => {
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).toContain("const lost = lostConnection(error) && navigator.onLine === false;");
    expect(view).toContain('lost && shown && "detail" in shown && shown.attempt === attempt ? shown : { attempt, error: loadFailure(error) }');
  });
});

describe("CR3: a GET is timed only until its answer starts, never while its body is read", () => {
  // Whatever the timer, it runs on the test's clock: AbortSignal.timeout too, if anything uses it.
  function fakeClock() {
    vi.useFakeTimers();
    vi.spyOn(AbortSignal, "timeout").mockImplementation((ms: number) => {
      const timer = new AbortController();
      setTimeout(() => timer.abort(new DOMException("The operation timed out.", "TimeoutError")), ms);
      return timer.signal;
    });
  }

  it("a slow body still arrives: the answer started in time", async () => {
    fakeClock();
    vi.stubGlobal("fetch", async (_url: string, init?: RequestInit) => {
      const body = new ReadableStream<Uint8Array>({
        start(stream) {
          init?.signal?.addEventListener("abort", () => stream.error(init.signal?.reason));
          setTimeout(() => {
            stream.enqueue(new TextEncoder().encode(JSON.stringify({ id: "d-1" })));
            stream.close();
          }, GET_TIMEOUT_MS + 30_000);
        },
      });
      return new Response(body, { status: 200 });
    });
    const pending = getDocument("d-1").catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(GET_TIMEOUT_MS + 30_000);
    expect(await pending).toEqual({ id: "d-1" });
  });

  it("no answer at all within 30 s is TOO_SLOW, status 0", async () => {
    fakeClock();
    vi.stubGlobal(
      "fetch",
      (_url: string, init?: RequestInit) =>
        new Promise((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(init.signal?.reason))),
    );
    const pending = getDocument("d-1").catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(GET_TIMEOUT_MS - 1);
    await vi.advanceTimersByTimeAsync(1);
    const caught = await pending;
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ status: 0, code: "timeout", message: TOO_SLOW });
  });

  it("the timer is cleared once the answer starts", async () => {
    fakeClock();
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ id: "d-1" }), { status: 200 }));
    await expect(getDocument("d-1")).resolves.toEqual({ id: "d-1" });
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe("CR1: a Settings draft keeps only what this admin changed, so another admin's change survives", () => {
  it("A changes the upload limit only, B turns auto-filing on, and A's restore keeps B's auto-filing", () => {
    const before: CustomerSettings = {
      customer_name: "Acme",
      max_upload_mb: 20,
      platform_max_upload_mb: 50,
      autofile: { granted: true, on: false },
    };
    const kept = settingsDraft(before, "30", false);
    expect(kept).toEqual({ uploadLimit: "30" });
    const afterB = { ...before, autofile: { granted: true, on: true } };
    expect(settingsFromDraft(kept, afterB)).toEqual({ text: "30", autofileOn: true });
    // Only auto-filing changed: the upload limit is not kept, so B's new limit would stand.
    expect(settingsDraft(before, "20", true)).toEqual({ autofile: true });
    expect(settingsFromDraft({ autofile: true }, { ...before, max_upload_mb: 40 })).toEqual({ text: "40", autofileOn: true });
  });

  it("the AI settings draft too: A raises a task's level, B locks it, and A's restore keeps B's lock", () => {
    const task = (more: Partial<AiTaskSetting>): AiTaskSetting => ({
      task: "ask",
      effective: "low",
      mine: null,
      organisation: "low",
      locked: false,
      allowed: ["low", "high", "max"],
      available: true,
      ...more,
    });
    const before: AiSettings = { tasks: [task({})], can_edit_organisation: true };
    const saved = orgDraftOf(before);
    const changes = changedOnly<{ level: TaskLevel; locked: boolean }>(saved, { ask: { level: "high", locked: false } });
    const kept = aiChangesToKeep(saved, changes);
    expect(kept).toEqual({ ask: { level: "high" } });
    const afterB: AiSettings = { tasks: [task({ locked: true })], can_edit_organisation: true };
    expect(orgDraftOver(orgDraftOf(afterB), kept, afterB)).toEqual({ ask: { level: "high", locked: true } });
    // And the other way round: A's lock only, B's new level stays.
    const lockOnly = aiChangesToKeep(saved, changedOnly<{ level: TaskLevel; locked: boolean }>(saved, { ask: { level: "low", locked: true } }));
    expect(lockOnly).toEqual({ ask: { locked: true } });
    const raisedByB: AiSettings = { tasks: [task({ organisation: "max", effective: "max" })], can_edit_organisation: true };
    expect(orgDraftOver(orgDraftOf(raisedByB), lockOnly, raisedByB)).toEqual({ ask: { level: "max", locked: true } });
  });
});

describe("SF3: leaving one admin tab drops only that tab's draft (structural)", () => {
  it("clears the Settings draft leaving Settings, and the AI settings draft leaving AI settings", () => {
    const view = read("components/admin/AdminView.tsx");
    expect(view).toContain('if (active === "settings") clearDraft(me.user.id, SETTINGS_DRAFT);');
    expect(view).toContain('if (active === "ai") clearDraft(me.user.id, aiSettingsDraft("organisation"));');
    expect(view).not.toMatch(/clearDraft\(me\.user\.id, SETTINGS_DRAFT\);\s*clearDraft\(me\.user\.id, aiSettingsDraft/);
  });
});
