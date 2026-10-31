import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LOST_PHONE_HELP } from "@/app/login/twoStep";
import { conversationLoadText } from "@/components/chat/logic";
import { waysLoadText } from "@/components/account/signInWays";
import { OFFLINE_TEXT, OfflineBanner, isOffline } from "@/components/shell/OfflineBanner";
import { STILL_LOADING_AFTER_MS, STILL_LOADING_RETRY, STILL_LOADING_TEXT, StillLoading } from "@/components/shell/StillLoading";
import {
  ApiError,
  GET_TIMEOUT_MS,
  TOO_SLOW,
  ask,
  getDocument,
  lostConnection,
  withTimeout,
} from "@/lib/api";
import { aiSaveErrorText } from "@/lib/aiSettings";
import { CONFIRM_DOWN, CONFIRM_NETWORK_FAILURE, LINK_GONE, confirmError } from "@/lib/emailLinks";
import { SERVER_PROBLEM } from "@/lib/errorWords";
import { leavePage, leavingPage } from "@/lib/sessionChannel";
import { WARN_BEFORE_MS, sessionEndingText, warningDelay } from "@/lib/operatorSession";

// Issue 468, builder A: timeouts and slow loads (A2), the offline line (A3), the code step's
// lost-phone line and the confirm page's words (A4), and load errors with a cause (A5), and the
// operator's warning before the session ends (A1).

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("a GET that gets no answer (lib/api.ts request)", () => {
  /** fetch that never answers, until its signal aborts. */
  function hang(seen: (init: RequestInit | undefined) => void) {
    vi.stubGlobal("fetch", (_url: string, init?: RequestInit) => {
      seen(init);
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(init.signal?.reason));
      });
    });
  }

  it("stops after 30 s and fails as a lost connection does (status 0), in plain words", async () => {
    const timeout = new AbortController();
    const asked: number[] = [];
    vi.spyOn(AbortSignal, "timeout").mockImplementation((ms: number) => {
      asked.push(ms);
      return timeout.signal;
    });
    let sentSignal: AbortSignal | null | undefined;
    hang((init) => (sentSignal = init?.signal));
    const pending = getDocument("d-1");
    expect(asked).toEqual([GET_TIMEOUT_MS]);
    expect(GET_TIMEOUT_MS).toBe(30_000);
    expect(sentSignal).toBe(timeout.signal);
    timeout.abort(new DOMException("The operation timed out.", "TimeoutError"));
    const caught = await pending.catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(ApiError);
    expect(caught).toMatchObject({ status: 0, code: "timeout", message: TOO_SLOW });
    expect(TOO_SLOW).toBe("Kaagaz is taking too long to answer. Check your connection and try again.");
    expect(lostConnection(caught)).toBe(true);
  });

  it("is untouched when the answer comes in time", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ id: "d-1" }), { status: 200 }));
    await expect(getDocument("d-1")).resolves.toEqual({ id: "d-1" });
  });

  it("keeps a lost connection a lost connection, and a server's refusal its own", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("Failed to fetch");
    });
    await expect(getDocument("d-1")).rejects.toBeInstanceOf(TypeError);
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ error: { code: "not_found", message: "No." } }), { status: 404 }));
    await expect(getDocument("d-1")).rejects.toMatchObject({ status: 404, code: "not_found" });
  });

  it("never times out Ask's answer or any change (not a GET), and leaves the caller's own signal alone", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    const mine = new AbortController();
    let sentSignal: AbortSignal | null | undefined;
    hang((init) => (sentSignal = init?.signal));
    const pending = ask("What is the total?", undefined, undefined, undefined, undefined, mine.signal);
    expect(timeout).not.toHaveBeenCalled();
    expect(sentSignal).toBe(mine.signal);
    mine.abort();
    const caught = await pending.catch((error: unknown) => error);
    expect(caught).not.toBeInstanceOf(ApiError); // the caller's Stop, as before (askStore.ts stopAsk)
    expect((caught as Error).name).toBe("AbortError");
  });

  it("combines a caller's signal with the timeout: either one stops it, with its own reason", () => {
    const timeout = new AbortController();
    const mine = new AbortController();
    const both = withTimeout(timeout.signal, mine.signal);
    expect(both.aborted).toBe(false);
    mine.abort("stopped");
    expect(both.aborted).toBe(true);
    expect(both.reason).toBe("stopped");
    const timeout2 = new AbortController();
    const both2 = withTimeout(timeout2.signal, new AbortController().signal);
    timeout2.abort("late");
    expect(both2.reason).toBe("late");
    expect(withTimeout(timeout2.signal)).toBe(timeout2.signal);
    const already = new AbortController();
    already.abort("before");
    expect(withTimeout(new AbortController().signal, already.signal).aborted).toBe(true);
  });

  it("has the GET timeout in request() only, with uploads sent by XHR and POST", () => {
    const api = read("lib/api.ts");
    expect(api).toContain('const timeout = method === "GET" ? AbortSignal.timeout(GET_TIMEOUT_MS) : null;');
    expect(api).toMatch(/if \(timeout\?\.aborted && !signal\?\.aborted && !\(caught instanceof ApiError\)\) \{\s*throw new ApiError\(0, "timeout", TOO_SLOW\);/);
  });
});

describe("a slow load says so (components/shell/StillLoading.tsx)", () => {
  it("waits about ten seconds, as Ask does, in a polite live region that is there from the start", () => {
    expect(STILL_LOADING_AFTER_MS).toBe(10_000);
    expect(`${STILL_LOADING_TEXT} ${STILL_LOADING_RETRY}.`).toBe(
      "Still loading — your connection may be slow. Keep waiting or Try again.",
    );
    const html = renderToStaticMarkup(createElement(StillLoading, { onRetry: () => undefined }));
    expect(html).toMatch(/^<div class="[^"]*" role="status" aria-live="polite"><\/div>$/);
  });

  it("sits beside the document view's, search's, the conversation's and the review queue's loading states", () => {
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).toMatch(/Loading the document…\s*<\/p>\s*<StillLoading onRetry=\{\(\) => setAttempt\(\(n\) => n \+ 1\)\} \/>/);
    const chat = read("components/chat/ChatScreen.tsx");
    expect(chat).toMatch(/Loading the conversation…[\s\S]{0,80}<StillLoading onRetry=\{\(\) => void open\(activeId\)\} \/>/);
    const search = read("components/search/SearchScreen.tsx");
    expect(search).toContain('{q !== "" && current === null ? <StillLoading onRetry={() => setAttempt((n) => n + 1)} /> : null}');
    const queue = read("components/review/ReviewQueue.tsx");
    expect(queue).toMatch(/Loading the review queue…\s*<\/p>\s*<StillLoading onRetry=\{\(\) => setAttempt\(\(n\) => n \+ 1\)\} \/>/);
  });

  it("starts the wait over on Try again, and never takes focus", () => {
    const source = read("components/shell/StillLoading.tsx");
    expect(source).toMatch(/setTries\(\(n\) => n \+ 1\);\s*onRetry\(\);/);
    expect(source).not.toContain(".focus(");
  });
});

describe("the offline line (components/shell/OfflineBanner.tsx)", () => {
  it("says what is shown and what happens next", () => {
    expect(OFFLINE_TEXT).toBe("You're offline. Showing what was already loaded. Kaagaz will try again when you're back.");
  });

  it("counts only an explicit false as offline", () => {
    expect(isOffline(false)).toBe(true);
    expect(isOffline(true)).toBe(false);
    expect(isOffline(undefined)).toBe(false);
  });

  it("is an empty polite live region while online (and on the server)", () => {
    expect(renderToStaticMarkup(createElement(OfflineBanner))).toBe('<div role="status" aria-live="polite"></div>');
  });

  it("listens to online and offline, reading navigator.onLine", () => {
    const source = read("components/shell/OfflineBanner.tsx");
    expect(source).toContain('window.addEventListener("online", onChange);');
    expect(source).toContain('window.addEventListener("offline", onChange);');
    expect(source).toContain("isOffline(navigator.onLine)");
  });

  it("sits in the app shell and the operator console", () => {
    expect(read("components/shell/AppShell.tsx")).toMatch(/<main[\s\S]*<OfflineBanner \/>[\s\S]*<\/main>/);
    expect(read("lib/operatorSession.tsx")).toMatch(/<OperatorContext\.Provider value=\{state\.me\}>\s*<OfflineBanner \/>/);
  });

  it("the document view keeps what it shows when asking again finds no connection, and asks again once online", () => {
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).toContain('lost && shown && "detail" in shown && shown.attempt === attempt ? shown : { attempt, error: loadFailure(error) }');
    expect(view).toContain('window.addEventListener("online", onOnline, { once: true });');
    expect(view).toContain('if (onOnline) window.removeEventListener("online", onOnline);');
  });

  it("knows a lost connection: fetch's TypeError or status 0, not a server's answer", () => {
    expect(lostConnection(new TypeError("Failed to fetch"))).toBe(true);
    expect(lostConnection(new ApiError(0, "network", "x"))).toBe(true);
    expect(lostConnection(new ApiError(503, "error", "x"))).toBe(false);
    expect(lostConnection(new Error("x"))).toBe(false);
  });
});

describe("the operator's warning before the session ends (lib/operatorSession.tsx)", () => {
  const ends = 1_800_000_000; // Unix seconds
  it("shows two minutes before expires_at, or at once when it is later than that", () => {
    expect(WARN_BEFORE_MS).toBe(120_000);
    expect(warningDelay(ends, ends * 1000 - 30 * 60_000)).toBe(28 * 60_000);
    expect(warningDelay(ends, ends * 1000 - 60_000)).toBe(0);
    expect(warningDelay(ends, ends * 1000 + 5_000)).toBe(0);
  });

  it("says to save, as the session cannot be made longer (no endpoint for it)", () => {
    expect(sessionEndingText(ends, ends * 1000 - 120_000)).toBe("Your session ends in 2 minutes. Save your changes now.");
    expect(sessionEndingText(ends, ends * 1000 - 50_000)).toBe("Your session ends in 1 minute. Save your changes now.");
    expect(sessionEndingText(ends, ends * 1000 + 1_000)).toBe("Your session ends in 1 minute. Save your changes now.");
    expect(read("lib/operatorSession.tsx")).not.toMatch(/Stay signed in/);
  });

  it("is a polite live region with Dismiss, shown above the console", () => {
    const source = read("lib/operatorSession.tsx");
    expect(source).toMatch(/function SessionEndingNotice[\s\S]*<div role="status" aria-live="polite">[\s\S]*onClick=\{\(\) => setDismissed\(expiresAt\)\}[\s\S]*Dismiss/);
    expect(source).toContain("<SessionEndingNotice expiresAt={state.me.session.expires_at} />");
  });
});

describe("the code step and the confirm page (A4)", () => {
  it("tells someone without their phone what to do, at sign-in and after Google", () => {
    expect(LOST_PHONE_HELP).toBe("Lost your phone? Ask another admin, or Kaagaz support, to reset your two-step sign-in.");
    expect(read("app/login/SignInForm.tsx")).toContain("{askCode && <p className=\"hint\">{LOST_PHONE_HELP}</p>}");
    expect(read("app/login/ProviderCodeStep.tsx")).toContain("{!expired && <p className=\"hint\">{LOST_PHONE_HELP}</p>}");
  });

  it("says to open the email link again when confirming failed, as the page already took the token", () => {
    expect(confirmError(new TypeError("Failed to fetch"))).toBe(CONFIRM_NETWORK_FAILURE);
    expect(confirmError(new ApiError(0, "network", "x"))).toBe(CONFIRM_NETWORK_FAILURE);
    expect(CONFIRM_NETWORK_FAILURE).toBe("Could not reach Kaagaz. Check your internet connection, then open the email link again.");
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(confirmError(new ApiError(500, "error", "x"))).toBe(CONFIRM_DOWN);
    expect(confirmError(new ApiError(400, "link_expired", "x"))).toBe(`${LINK_GONE} Sign in to get a new link.`);
  });
});

describe("load errors with a cause and Try again (A5)", () => {
  it("the conversation says why it did not load", () => {
    expect(conversationLoadText(new TypeError("Failed to fetch"))).toBe(
      "Could not load this conversation. Check your internet connection and try again.",
    );
    expect(conversationLoadText(new ApiError(0, "timeout", TOO_SLOW))).toBe(`Could not load this conversation. ${TOO_SLOW}`);
    expect(conversationLoadText(new ApiError(502, "error", "Something went wrong."))).toBe(
      "Could not load this conversation. Kaagaz had a problem. Try again in a minute.",
    );
    const chat = read("components/chat/ChatScreen.tsx");
    expect(chat).toMatch(/<p className="error-text" role="alert">\s*\{threadError\}\s*<\/p>/);
    expect(chat.match(/setThreadError\(conversationLoadText\(error\)\);/g)).toHaveLength(2);
  });

  it("Ways to sign in says why, with Try again (not when Google is switched off)", () => {
    expect(waysLoadText(new TypeError("Failed to fetch"))).toBe(
      "Could not load your ways to sign in. Check your internet connection and try again.",
    );
    expect(waysLoadText(new ApiError(500, "error", "x"))).toBe(
      "Could not load your ways to sign in. Kaagaz had a problem. Try again in a minute.",
    );
    const ways = read("components/account/WaysToSignIn.tsx");
    expect(ways).toContain("setFailure(off ? { text: OFF, retry: false } : { text: waysLoadText(caught), retry: true });");
    expect(ways).toMatch(/\{failure\.retry && \([\s\S]*setAttempt\(\(n\) => n \+ 1\);[\s\S]*Try again/);
    expect(ways).toContain("}, [attempt]);");
  });
});

describe("a page left on purpose is not stopped by the upload prompt (lib/sessionChannel.ts)", () => {
  it("leavePage marks the tab as leaving, and the upload guard lets it go", () => {
    const replace = vi.fn();
    vi.stubGlobal("window", { location: { replace } });
    expect(leavingPage()).toBe(false);
    leavePage("/login?ended=1");
    expect(replace).toHaveBeenCalledWith("/login?ended=1");
    expect(leavingPage()).toBe(true);
    expect(read("components/documents/UploadArea.tsx")).toMatch(
      /const warn = \(event: BeforeUnloadEvent\) => \{\s*if \(leavingPage\(\)\) return;[^\n]*\s*event\.preventDefault\(\);/,
    );
  });
});

describe("a refused AI settings save says why in plain words (lib/aiSettings.ts)", () => {
  it("never passes a proxy's page through, and keeps the backend's own words", () => {
    expect(aiSaveErrorText(new ApiError(502, "error", "Something went wrong."))).toBe(SERVER_PROBLEM);
    expect(aiSaveErrorText(new ApiError(400, "invalid", "Choose a level."))).toBe("Choose a level.");
    expect(aiSaveErrorText(new ApiError(400, "locked", "x"))).toBe("Your admin has set this, so it can't be changed here.");
    expect(aiSaveErrorText(new TypeError("Failed to fetch"))).toBe("Could not save. Check your connection, then try again.");
  });
});
