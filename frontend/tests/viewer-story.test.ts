import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UploadState } from "@/components/documents/logic";
import { codePoints, countText, STORY_HINT, storyBody, storyFailure } from "@/components/viewer/story";
import { storyHref, storyProblem, storyTarget, tidyStory } from "@/components/viewer/story";
import { ApiError, deleteMyStory, getMyStory, saveMyStory } from "@/lib/api";

const PAPA = "Papa ne 2018 me jo Daregaon wali jamin ka 7/12 diya tha, bank loan ke liye";

describe("counting characters as the server does (code points)", () => {
  it("counts an emoji once, not as two UTF-16 units, and Devanagari letter by letter", () => {
    expect(codePoints("📄")).toBe(1);
    expect("📄".length).toBe(2);
    expect(codePoints("जमीन")).toBe(4); // ज म ी न: the vowel sign is its own code point
    expect(codePoints("सात बारा")).toBe(8);
  });

  it("lets 1,000 characters of emoji through and refuses 1,001, though they are about 2,000 UTF-16 units", () => {
    expect(storyProblem("7" + "📄".repeat(999))).toBeNull();
    expect(storyProblem("7" + "📄".repeat(1000))).toBe("A story can be at most 1000 characters.");
    expect(storyProblem("📄📄")).toBe("The story needs at least one letter or digit.");
    expect(storyProblem("ज".repeat(1000))).toBeNull();
    expect(storyProblem("ज".repeat(1001))).toBe("A story can be at most 1000 characters.");
  });

  it("counts what will be stored, and says how many too many", () => {
    expect(countText("")).toBe("0 of 1,000 characters");
    expect(countText("  papa   ne  ")).toBe("7 of 1,000 characters");
    expect(countText("a".repeat(1000))).toBe("1,000 of 1,000 characters");
    expect(countText("a".repeat(1012))).toBe("1,012 of 1,000 characters, 12 too many");
  });
});

describe("tidying, as check_story (the server tidies again and wins)", () => {
  it("makes white space runs one space, trims, and composes (NFC)", () => {
    expect(tidyStory("  papa\n\n ne\t2018  ")).toBe("papa ne 2018");
    expect(tidyStory("cafe\u0301")).toBe("café");
    expect(codePoints(tidyStory("cafe\u0301"))).toBe(4);
  });

  it("drops controls, direction overrides, zero-width and private-use characters", () => {
    expect(tidyStory("ab\u202Ecd\u200Bef\u0007\uE000\uFEFF")).toBe("abcdef");
    expect(tidyStory("a\uD800b")).toBe("ab"); // a lone surrogate
  });

  it("keeps a joiner only after a virama, and drops variation selectors", () => {
    expect(tidyStory("क्\u200Dष")).toBe("क्\u200Dष");
    expect(tidyStory("👨\u200D👩")).toBe("👨👩");
    expect(tidyStory("❤\uFE0F")).toBe("❤");
  });

  it("keeps at most 4 combining marks on one letter", () => {
    expect(tidyStory("a" + "\u0301".repeat(9))).toBe(("a" + "\u0301".repeat(4)).normalize("NFC"));
  });
});

describe("the text check, in the server's words", () => {
  it("passes a Romanised, a Devanagari and a digits-only story", () => {
    for (const text of [PAPA, "पापा ने 2018 में दरेगाव वाली जमीन का ७/१२ दिया था", "7/12"]) {
      expect(storyProblem(text), text).toBeNull();
    }
  });

  it("says why", () => {
    expect(storyProblem("")).toBe("Write the story first.");
    expect(storyProblem(" \n\t\u200B ")).toBe("Write the story first.");
    expect(storyProblem("!!! ... ?")).toBe("The story needs at least one letter or digit.");
    expect(storyProblem("\u3164 \u3164")).toBe("The story needs at least one letter or digit."); // Hangul fillers
    // Only the tidied text is sent, so spaces pasted past 4,000 never reach the server.
    expect(storyProblem("a" + " ".repeat(5000) + "b")).toBeNull();
    expect(storyBody("a" + " ".repeat(5000) + "b", "u1").story).toBe("a b");
  });

  it("shows the council's hint", () => {
    expect(STORY_HINT).toBe(`Write how you remember this document, e.g. '${PAPA}'. Only you can see it.`);
  });
});

describe("the requests: user_id where S2 reads it", () => {
  const calls: { url: string; method: string; csrf: string | null; body: unknown }[] = [];
  const story = { family_id: "f1", story: PAPA, embedded: true, created_at: "x", updated_at: "x" };

  function answer(status: number, body?: unknown) {
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      const csrf = new Headers(init.headers).get("X-Requested-With");
      calls.push({ url, method: init.method ?? "GET", csrf, body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(body === undefined ? null : JSON.stringify(body), { status });
    });
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    calls.length = 0;
  });

  it("reads with GET, no CSRF header and no user_id", async () => {
    answer(200, { story: null });
    await expect(getMyStory("d1")).resolves.toEqual({ story: null });
    expect(calls).toEqual([{ url: "/api/documents/d1/my-story", method: "GET", csrf: null, body: undefined }]);
  });

  it("saves with PUT: the tidied story and user_id in the body, the CSRF header", async () => {
    answer(201, { story });
    expect(storyBody(`  ${PAPA} \n`, "u1")).toEqual({ story: PAPA, user_id: "u1" });
    await expect(saveMyStory("d1", storyBody(PAPA, "u1"))).resolves.toEqual({ story });
    expect(calls).toEqual([
      { url: "/api/documents/d1/my-story", method: "PUT", csrf: "kaagaz", body: { story: PAPA, user_id: "u1" } },
    ]);
  });

  it("deletes with DELETE: user_id in the query, no body, the CSRF header", async () => {
    answer(204);
    await expect(deleteMyStory("d1", "u 1")).resolves.toBeUndefined();
    expect(calls).toEqual([{ url: "/api/documents/d1/my-story?user_id=u+1", method: "DELETE", csrf: "kaagaz", body: undefined }]);
  });

  it("keeps the id inside its path segment", async () => {
    answer(200, { story: null });
    await getMyStory("../x");
    expect(calls.map((call) => call.url)).toEqual(["/api/documents/..%2Fx/my-story"]);
  });

  it("turns an error reply into an ApiError with the server's code", async () => {
    answer(409, { error: { code: "session_changed", message: "Not saved. A different account..." } });
    await expect(saveMyStory("d1", storyBody(PAPA, "u1"))).rejects.toMatchObject({ status: 409, code: "session_changed" });
  });
});

describe("every status code as what the screen shows", () => {
  const error = (status: number, code: string, message = "Server words.") => new ApiError(status, code, message);
  const SESSION = "A different account has signed in on this browser since this page opened. Reload the page to see who is signed in now.";

  it("puts a refused text (400) by the box, in the server's words", () => {
    expect(storyFailure(error(400, "bad_request", "Write the story first."), "save")).toEqual({
      kind: "field",
      message: "Write the story first.",
    });
  });

  it("says a 404 means the document or the story is gone", () => {
    expect(storyFailure(error(404, "not_found"), "save")).toEqual({
      kind: "gone",
      message: "Not saved. This document isn't there any more, or it is in Trash. Reload the page.",
    });
    expect(storyFailure(error(404, "not_found"), "load").message).toMatch(/^Your story could not be loaded\. This document/);
    expect(storyFailure(error(404, "not_found"), "delete")).toEqual({ kind: "gone", message: "Your story isn't there any more." });
  });

  it("says another account signed in (409 session_changed), for a save and a delete", () => {
    expect(storyFailure(error(409, "session_changed"), "save")).toEqual({ kind: "alert", message: `Not saved. ${SESSION}` });
    expect(storyFailure(error(409, "session_changed"), "delete")).toEqual({ kind: "alert", message: `Not deleted. ${SESSION}` });
  });

  it("uses the server's words for 403, other 409s, 429 and 503, and its own for 401 and 500", () => {
    for (const [status, code] of [[403, "csrf"], [409, "conflict"], [429, "rate_limited"], [503, "backend_unavailable"], [400, "bad_request"]] as const) {
      expect(storyFailure(error(status, code), status === 400 ? "delete" : "save"), `${status}`).toEqual({
        kind: "alert",
        message: `${status === 400 ? "Not deleted." : "Not saved."} Server words.`,
      });
    }
    expect(storyFailure(error(401, "not_signed_in"), "save").message).toBe("Not saved. Please sign in again.");
    expect(storyFailure(error(500, "internal_error"), "delete").message).toBe("Not deleted. Something went wrong on our side. Try again.");
  });

  it("says check the connection when the server was not reached", () => {
    const offline = { kind: "alert", message: "Not saved. Check your internet connection, then try again." };
    expect(storyFailure(new TypeError("Failed to fetch"), "save")).toEqual(offline);
    expect(storyFailure(new ApiError(0, "network", "x"), "save")).toEqual(offline);
  });
});

describe("after an upload: a link to the story section, never a dialog", () => {
  it("is offered for every finished file, and not while it uploads, when it failed or is in Trash", () => {
    const states: [UploadState, string | null][] = [
      [{ kind: "done", documentId: "d1" }, "d1"],
      [{ kind: "restored", documentId: "d2", existingName: "a.pdf" }, "d2"],
      [{ kind: "duplicate", documentId: "d3", existingName: "a.pdf", inTrash: false }, "d3"],
      [{ kind: "duplicate", documentId: "d4", existingName: "a.pdf", inTrash: true }, null],
      [{ kind: "restoring", documentId: "d5", existingName: "a.pdf" }, null],
      [{ kind: "waiting" }, null],
      [{ kind: "sending", fraction: 0.5 }, null],
      [{ kind: "error", message: "Not uploaded." }, null],
    ];
    for (const [state, target] of states) expect(storyTarget(state), state.kind).toBe(target);
    expect(storyHref("d1")).toBe("/documents/d1#your-story");
  });
});

describe("the story screens", () => {
  const files = ["components/viewer/story.ts", "app/(app)/documents/[id]/StoryPanel.tsx", "components/documents/UploadArea.tsx"];

  it("log nothing and set no HTML", () => {
    for (const file of files) expect(readFileSync(file, "utf8"), file).not.toMatch(/console\.|innerHTML/);
  });
});
