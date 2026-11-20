import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { aiChangesToKeep, orgDraftOver, settingsDraft, settingsFromDraft } from "@/components/admin/logic";
import { waysLoadText } from "@/components/account/signInWays";
import { ASK_DRAFT, askDraft, askHref, askWithWordsHref, conversationLoadText } from "@/components/chat/logic";
import { operatorErrorMessage } from "@/components/operator/logic";
import { storyFailure } from "@/components/viewer/story";
import { ViewerFailed } from "@/components/viewer/ViewerBoundary";
import { endedPath, pathAfterSignIn, SEARCH_DRAFT, searchToKeep } from "@/components/shell/gate";
import { ApiError, GET_TIMEOUT_MS, TOO_SLOW, getDocument } from "@/lib/api";
import { changedOnly, orgDraftOf } from "@/lib/aiSettings";
import { DRAFT_KEPT, clearDrafts, readDraft, saveDraft, saveDraftSoon } from "@/lib/drafts";
import { NETWORK_FAILURE } from "@/lib/emailLinks";
import { SERVER_PROBLEM, errorWords } from "@/lib/errorWords";
import { SESSION_ENDING_TEXT } from "@/lib/operatorSession";
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
    // Changed on purpose (#730): one search, no "in".
    expect(screen).toMatch(/if \(q === "" && typeof kept === "string" && kept\.trim\(\)\) router\.replace\(searchHref\(kept\)\)/);
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

describe("SF2: an operator or story timeout says it is slow, not to check the internet", () => {
  const timeout = new ApiError(0, "timeout", TOO_SLOW);
  it("the operator console says TOO_SLOW for a timeout, the network words for a lost connection", () => {
    expect(operatorErrorMessage(timeout)).toBe(TOO_SLOW);
    expect(operatorErrorMessage(new ApiError(0, "network", "x"))).toBe(operatorErrorMessage(new TypeError("Failed to fetch")));
  });

  it("a story says TOO_SLOW after what did not happen", () => {
    expect(storyFailure(timeout, "load")).toEqual({ kind: "alert", message: `Your story could not be loaded. ${TOO_SLOW}` });
    expect(storyFailure(new TypeError("Failed to fetch"), "save").message).not.toContain(TOO_SLOW);
  });
});

describe("UX4: a restored draft says so, in one muted line (structural)", () => {
  it("has the words", () => {
    expect(DRAFT_KEPT).toBe("We kept what you were typing. It hasn't been sent.");
  });

  it("Ask shows it while the restored words are unchanged; editing or sending takes it away", () => {
    const chat = read("components/chat/ChatScreen.tsx");
    expect(chat).toMatch(/setDraft\(kept\);\s*setKeptNote\(kept\);/);
    expect(chat).toContain("setKeptNote((shown) => (shown !== null && shown !== text ? null : shown));");
    expect(chat).toMatch(/const onAsk = async \([^)]*\): Promise<boolean> => \{\s*setKeptNote\(null\);/);
    expect(chat).toContain("{keptNote !== null ? <p className={styles.composerNote}>{DRAFT_KEPT}</p> : null}");
  });

  it("Add a user shows it when a draft came back, until an edit or a send (structural)", () => {
    const users = read("components/admin/UsersPanel.tsx");
    expect(users).toContain("const [keptNote, setKeptNote] = useState(kept !== null);");
    expect(users).toMatch(/const edit = \(next: NewUserInput\) => \{\s*setKeptNote\(false\);\s*setInput\(next\);/);
    expect(users).toMatch(/if \(busy\) return;[^\n]*\n\s*setKeptNote\(false\);/);
    expect(users).toContain('{keptNote && <p className="muted">{DRAFT_KEPT}</p>}');
    // No live region, no focus move: a plain line.
    for (const source of [read("components/chat/ChatScreen.tsx"), users]) {
      expect(source).not.toMatch(/role="status"[^>]*>\{DRAFT_KEPT\}/);
    }
  });
});

describe("UX1: cancelling the leave question keeps focus on the tab still open (structural)", () => {
  it("Tabs' onChange says whether the tab changed, and focus moves only then", () => {
    const tabs = read("components/admin/Tabs.tsx");
    expect(tabs).toContain("onChange(id: T): boolean;");
    expect(tabs).toContain("if (onChange(tab.id)) document.getElementById(tabId(prefix, tab.id))?.focus();");
  });

  it("AdminView's openTab returns false when the admin stays, true once the tab opens", () => {
    const view = read("components/admin/AdminView.tsx");
    expect(view).toMatch(/function openTab\(next: TabId\): boolean \{/);
    expect(view).toContain("if (!tabChangeAllowed(unsaved, () => window.confirm(LEAVE_UNSAVED))) return false;");
    expect(view).toMatch(/setActive\(next\);\s*return true;/);
    expect(read("components/operator/OperatorConsole.tsx")).toMatch(/function choose\(id: TabId\): boolean \{[\s\S]*?return true;/);
  });
});

describe("PT1 / SF4 / UX2 / CR2: Clear filters with the Date panel open empties its inputs (structural)", () => {
  // Not a key on the panel: a pick changes the filters too, and a remount would take focus out of
  // the date being typed. The inputs follow the filters' dates when those change from outside.
  it("the Date panel's inputs start over whenever the filters' dates change from outside", () => {
    const panel = read("components/admin/ActivityPanel.tsx");
    expect(panel).toMatch(
      /if \(shown\.from !== startFrom \|\| shown\.to !== startTo\) \{\s*setShown\(\{ from: startFrom, to: startTo \}\);\s*setFrom\(startFrom \?\? ""\);\s*setTo\(startTo \?\? ""\);/,
    );
    expect(panel).not.toMatch(/<ActivityDates\s+key=/);
    // The operator's Activity has no Date panel.
    expect(read("components/operator/ActivityView.tsx")).not.toContain("ActivityDates");
  });
});

describe("UX3: the offline line stays in view however far the screen is scrolled (structural)", () => {
  it("is sticky at the top, above the screen, in the app and the operator console (one component)", () => {
    const css = read("components/shell/Shell.module.css");
    expect(css).toMatch(/\.offlineBanner \{\s*position: sticky;\s*top: 0;\s*z-index: 1;\s*\}/);
    // Sticky on the live region itself: a sticky child of a box its own height never sticks.
    expect(read("components/shell/OfflineBanner.tsx")).toContain(
      '<div role="status" aria-live="polite" className={offline ? styles.offlineBanner : undefined}>',
    );
  });
});

describe("SF5: an unknown 4xx with no code of the API's (a proxy's page) is SERVER_PROBLEM", () => {
  it("never shows the bare \"Something went wrong.\"", () => {
    for (const status of [400, 405, 413, 418, 422]) {
      expect(errorWords(new ApiError(status, "error", "Something went wrong.")), String(status)).toBe(SERVER_PROBLEM);
    }
    // The backend's own words for its own code stay.
    expect(errorWords(new ApiError(400, "invalid_email", "That e-mail is not valid."))).toBe("That e-mail is not valid.");
  });
});

describe("CR5: reading a draft first writes that screen's save still waiting", () => {
  it("a quick remount keeps the last keystrokes", () => {
    vi.useFakeTimers();
    saveDraftSoon(A, "ask", { typed: "What is the tot" });
    saveDraftSoon(A, "ask", { typed: "What is the total?" });
    expect(readDraft(A, "ask")).toEqual({ typed: "What is the total?" });
    vi.runAllTimers();
    expect(readDraft(A, "ask")).toEqual({ typed: "What is the total?" });
  });

  it("only that screen's: another screen's waiting save still waits", () => {
    vi.useFakeTimers();
    saveDraftSoon(A, "summary", { text: "waiting" });
    readDraft(A, "ask");
    expect(storage.items.size).toBe(0);
    vi.runAllTimers();
    expect(storage.items.size).toBe(1);
  });
});

describe("PT5: readDraft reads only its own key, and leaves other people's drafts to the sign-in", () => {
  it("drops nobody else's draft by reading (lib/session.tsx does that once who is signed in is known)", () => {
    saveDraft(A, "ask", { typed: "A's" });
    expect(readDraft(B, "ask")).toBeNull();
    expect(readDraft(A, "ask")).toEqual({ typed: "A's" });
    expect(read("lib/drafts.ts")).not.toMatch(/export function readDraft[\s\S]*?keepDraftsOf\(userId\)[\s\S]*?\n\}/);
    expect(read("lib/session.tsx")).toMatch(/if \(signedInId !== null\) keepDraftsOf\(signedInId\)/);
  });
});

describe("CR6: the viewer's Download link is a download, so it never fires the upload leave prompt", () => {
  it("carries the download attribute", () => {
    const html = renderToStaticMarkup(createElement(ViewerFailed, { onRetry: () => undefined, download: "/api/documents/d-1/file" }));
    expect(html).toMatch(/<a class="btn" href="\/api\/documents\/d-1\/file" download="">Download<\/a>/);
  });
});

describe("PT2 / UX9: errorWords takes any failure; one network wording everywhere (NETWORK_FAILURE)", () => {
  it("anything that is not an ApiError is the network wording", () => {
    expect(errorWords(new TypeError("Failed to fetch"))).toBe(NETWORK_FAILURE);
    expect(errorWords(undefined)).toBe(NETWORK_FAILURE);
    expect(errorWords(new ApiError(0, "timeout", TOO_SLOW))).toBe(TOO_SLOW); // the slow case stays its own
  });

  it("no screen keeps its own ternary and offline sentence", () => {
    for (const file of [
      "components/account/signInWays.ts",
      "components/admin/ActivityPanel.tsx",
      "components/admin/AiSettingsPanel.tsx",
      "components/admin/DepartmentsPanel.tsx",
      "components/admin/logic.ts",
      "components/admin/MembersTable.tsx",
      "components/admin/NumbersPanel.tsx",
      "components/admin/SettingsPanel.tsx",
      "components/admin/UsagePanel.tsx",
      "components/admin/UsersPanel.tsx",
      "components/chat/logic.ts",
    ]) {
      expect(read(file), file).not.toMatch(/instanceof ApiError\s*\?\s*errorWords\(/);
    }
  });

  it("the departments' load error says what and why; the conversation and ways-to-sign-in loads too", () => {
    expect(read("components/admin/DepartmentsPanel.tsx")).toContain("setError(`The departments did not load. ${errorWords(caught)}`)");
    expect(conversationLoadText(new TypeError("x"))).toBe(`Could not load this conversation. ${NETWORK_FAILURE}`);
    expect(waysLoadText(new TypeError("x"))).toBe(`Could not load your ways to sign in. ${NETWORK_FAILURE}`);
  });
});

describe("PT3: Organisations loads with the shared skeleton rows (structural)", () => {
  it("uses LoadingRows, not its own copy", () => {
    const view = read("components/operator/OrganisationsView.tsx");
    expect(view).toContain('{!orgs && !failed && <LoadingRows what="the organisations" />}');
    expect(view).not.toContain("SKELETON_ROWS");
  });
});

describe("PT4: StillLoading waits Ask's own time (one constant)", () => {
  it("imports STILL_LOOKING_AFTER_MS instead of a second constant", () => {
    const source = read("components/shell/StillLoading.tsx");
    expect(source).toContain('import { STILL_LOOKING_AFTER_MS } from "@/components/chat/logic";');
    expect(source).toContain("STILL_LOOKING_AFTER_MS)");
    expect(source).not.toContain("STILL_LOADING_AFTER_MS");
  });
});

describe("PT7: one ?q= contract for Ask, and one lost-connection test", () => {
  it("askWithWordsHref sits next to askHref; only send=1 sends", () => {
    expect(askWithWordsHref("rent paid & GST")).toBe("/chat?q=rent%20paid%20%26%20GST");
    expect(askHref("rent")).toBe("/chat?q=rent&send=1");
    expect(read("components/documents/insideSearch.ts")).not.toContain("export function askWithWordsHref");
  });

  it("the upload retry uses lostConnection()", () => {
    expect(read("lib/api.ts")).toContain("const lost = lostConnection(error);");
  });
});

describe("UX5: the operator's banners sit inside OperatorShell, after the skip link (structural)", () => {
  it("renders the offline line and the session warning between the skip link and the top bar", () => {
    const shell = read("components/operator/OperatorShell.tsx");
    expect(shell).toMatch(
      /Skip to content\s*<\/a>\s*<OfflineBanner \/>\s*<SessionEndingNotice expiresAt=\{session\.expires_at\} \/>\s*<header/,
    );
    expect(read("lib/operatorSession.tsx")).toMatch(/<OperatorContext\.Provider value=\{state\.me\}>\{children\}<\/OperatorContext\.Provider>/);
  });
});

describe("UX6: the session warning never shows a count that goes stale", () => {
  it("says under 2 minutes", () => {
    expect(SESSION_ENDING_TEXT).toBe("Your session ends in under 2 minutes. Save your changes now.");
  });
});

describe("UX7: Search's slow notice starts over for a new search (structural)", () => {
  it("is keyed on the words and the tab", () => {
    expect(read("components/search/SearchScreen.tsx")).toContain(
      "<StillLoading key={q} onRetry={() => setAttempt((n) => n + 1)} />", // #730: one search, keyed on its words
    );
  });
});

describe("UX10: an organisation dialog's confirmation also goes to the page's Toast (structural)", () => {
  it("survives the dialog closing", () => {
    const view = read("components/operator/OrganisationsView.tsx");
    expect(view).toContain("<OrgFeatureSwitchesDialog org={switchesOf} onClose={() => setSwitchesOf(null)} onDone={setDone} />");
    expect(view).toContain("<OrgSessionTimeoutDialog org={timeoutOf} onClose={() => setTimeoutOf(null)} onDone={setDone} />");
    for (const file of ["components/operator/FeatureSwitches.tsx", "components/operator/SessionTimeout.tsx"]) {
      expect(read(file), file).toMatch(/setDone\(message\);\s*onDone\(message\);/);
    }
  });
});
