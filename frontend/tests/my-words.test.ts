import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { addBody, changeBody, duplicateQuestion, failedState, limitNote } from "@/components/words/words";
import { retryBody, savedState, textProblem, understoodNotes, wordGroups } from "@/components/words/words";
import { addMyWord, ApiError, changeMyWord, deleteMyWord, listMyWords } from "@/lib/api";
import type { MyWord } from "@/lib/types";

const at = "2026-10-03T10:00:00Z";
const word = (id: string, text: string, meaning: string): MyWord =>
  ({ id, word: text, meaning, source: "manual", built_in: false, created_at: at, updated_at: at });

describe("the text check, as the server's (it decides)", () => {
  it("passes a Romanised, a Devanagari and a two-part word, a meaning with a comma, and counts characters", () => {
    for (const text of ["malak", "बोजा", "  saat   bara ", "𝐀".repeat(40)]) expect(textProblem(text, "word")).toBeNull();
    expect(textProblem("owner, landlord", "meaning")).toBeNull();
  });

  it("says why, in the server's words", () => {
    expect(textProblem("   ", "word")).toBe("Type the word.");
    expect(textProblem("a".repeat(41), "word")).toBe("The word can be at most 40 characters.");
    expect(textProblem("a".repeat(81), "meaning")).toBe("The meaning can be at most 80 characters.");
    expect(textProblem("<b>", "word")).toBe("Use only letters, digits, spaces and hyphens in the word.");
    expect(textProblem("a, b", "word")).toBe("Use only letters, digits, spaces and hyphens in the word.");
    expect(textProblem("a & b", "meaning")).toBe("Use only letters, digits, spaces, commas and hyphens in the meaning.");
    expect(textProblem("123", "word")).toBe("The word needs at least one letter.");
    expect(textProblem("a b c d", "word")).toBe("A word can have at most 3 parts.");
    expect(textProblem("ab\u202Ecd", "word")).toMatch(/^Use only letters/); // a bidi override
  });

});

describe("the requests", () => {
  const draft = { word: "  malak ", meaning: "owner  " };

  it("adds with tidied text and who this tab signed in; files only when there are some, at most 20", () => {
    expect(addBody(draft, "u1")).toEqual({ word: "malak", meaning: "owner", user_id: "u1" });
    const ids = Array.from({ length: 25 }, (_, n) => `d${n}`);
    expect(addBody(draft, "u1", ids).document_ids).toEqual(ids.slice(0, 20));
  });

  it("turns the dialog's choice into the request to send again", () => {
    const body = addBody(draft, "u1", ["d1"]);
    expect(retryBody(body, { kind: "add_too" })).toEqual({ ...body, confirm_another: true });
    expect(retryBody(body, { kind: "replace", id: "w9" })).toEqual({ ...body, replace_id: "w9" });
    expect(retryBody(body, { kind: "cancel" })).toBeNull();
    expect(body).not.toHaveProperty("confirm_another");
  });

  it("changes only what changed", () => {
    const item = { word: "malak", meaning: "boss" };
    expect(changeBody(item, { word: "malak ", meaning: "owner" }, "u1")).toEqual({ meaning: "owner", user_id: "u1" });
    expect(changeBody(item, { word: "maalak", meaning: "boss" }, "u1")).toEqual({ word: "maalak", user_id: "u1" });
    expect(changeBody(item, { word: " malak", meaning: "boss" }, "u1")).toBeNull();
    // A2 review: PATCH asks the same question; the choice is sent the same way.
    const body = changeBody(item, { word: "malak", meaning: "owner" }, "u1") ?? { user_id: "" };
    expect(retryBody(body, { kind: "add_too" })).toEqual({ meaning: "owner", user_id: "u1", confirm_another: true });
    expect(retryBody(body, { kind: "replace", id: "w2" })).toEqual({ meaning: "owner", user_id: "u1", replace_id: "w2" });
  });
});

describe("a reply as what the screen says", () => {
  const saved = (outcome: "created" | "already" | "replaced", warning: "built_in" | null = null) =>
    savedState({ word: word("w1", "malak", "owner"), outcome, warning }).message;

  it("names each outcome (201 created, 200 already or replaced)", () => {
    expect(saved("created")).toBe("Saved 'malak' as 'owner'.");
    expect(saved("already")).toBe("'malak' as 'owner' is already in your words.");
    expect(saved("replaced")).toBe("Saved 'malak' as 'owner' in place of the old meaning.");
    expect(saved("created", "built_in")).toMatch(/^Saved 'malak' as 'owner'\. Chat already knows this word/);
  });

  it("asks on 409 word_has_meaning with the meanings there; else says the server's words", () => {
    const details = { existing: [{ id: "w1", meaning: "boss", extra: 1 }] };
    const asked = failedState(new ApiError(409, "word_has_meaning", "…", details));
    expect(asked).toEqual({ kind: "ask", existing: [{ id: "w1", meaning: "boss" }] });
    for (const details of [undefined, {}, { existing: [] }, { existing: [{ id: 1 }] }]) {
      const said = failedState(new ApiError(409, "word_has_meaning", "Asked.", details));
      expect(said).toEqual({ kind: "refused", message: "Asked." });
    }
  });

  it("gives the server's words for the limits, the text, the session and the rate limit", () => {
    const refusals = [[409, "too_many_words"], [409, "too_many_meanings"], [409, "conflict"],
      [409, "session_changed"], [400, "bad_request"], [429, "rate_limited"]] as const;
    for (const [status, code] of refusals) {
      expect(failedState(new ApiError(status, code, `said ${code}`))).toEqual({ kind: "refused", message: `said ${code}` });
    }
  });

  it("has its own words for 404 and for no connection", () => {
    const gone = { kind: "refused", message: "That word or file isn't there any more. Reload the page." };
    expect(failedState(new ApiError(404, "not_found", "We couldn't find that."))).toEqual(gone);
    const offline = { kind: "refused", message: "Not saved. Check your internet connection, then try again." };
    expect(failedState(new TypeError("Failed to fetch"))).toEqual(offline);
    expect(failedState(new ApiError(0, "network", "x"))).toEqual(offline);
  });

  it("asks the council's question", () => {
    expect(duplicateQuestion("malak", "owner", [{ id: "w1", meaning: "boss" }])).toBe(
      "You already added 'malak' as 'boss'. Add 'owner' as another meaning too? Two meanings for one word can confuse chat.",
    );
    const two = [{ id: "w1", meaning: "boss" }, { id: "w2", meaning: "master" }];
    expect(duplicateQuestion("malak", "owner", two)).toMatch(/^You already added 'malak' as 'boss' and 'master'\. /);
  });
});

describe("the list", () => {
  const items = [word("w1", "malak", "boss"), word("w2", "Malak", "owner"), word("w3", "बोजा", "mortgage charge")];

  it("groups meanings by word, folded, and filters on either side", () => {
    const grouped = wordGroups(items, "").map((g) => [g.word, g.meanings.map((m) => m.id)]);
    expect(grouped).toEqual([["malak", ["w1", "w2"]], ["बोजा", ["w3"]]]);
    expect(wordGroups(items, " MAL ").map((g) => g.word)).toEqual(["malak"]);
    expect(wordGroups(items, "mortgage").map((g) => g.word)).toEqual(["बोजा"]);
    expect(wordGroups(items, "zzz")).toEqual([]);
  });

  it("notes the 500-word limit only near it", () => {
    expect(limitNote(10)).toBeNull();
    expect(limitNote(490)).toBe("You have 490 of 500 words.");
    expect(limitNote(500)).toBe("You have 500 words, the most you can keep. Delete some to add more.");
  });
});

describe("the chat note", () => {
  it("shows nothing before A3 sends understood_words, or when it is empty or odd", () => {
    const odd = [{ word: "", meaning: "x" }, { word: 1, meaning: "x" }, null] as never;
    for (const answer of [{}, { understood_words: [] }, { understood_words: odd }]) expect(understoodNotes(answer)).toEqual([]);
  });

  it("says each word as text, at most 10", () => {
    const one = understoodNotes({ understood_words: [{ word: "malak", meaning: "owner" }] });
    expect(one).toEqual(["Understood 'malak' as 'owner' (your word)"]);
    expect(understoodNotes({ understood_words: [{ word: "Jalna", meaning: "district", origin: "organization" }] }))
      .toEqual(["Understood 'Jalna' as 'district' (organization word)"]);
    const many = Array.from({ length: 12 }, (_, n) => ({ word: `w${n}`, meaning: "<b>x</b>" }));
    const notes = understoodNotes({ understood_words: many });
    expect(notes).toHaveLength(10);
    expect(notes[0]).toBe("Understood 'w0' as '<b>x</b>' (your word)");
  });
});

describe("the my words calls", () => {
  const calls: { url: string; method: string; csrf: string | null; body: unknown }[] = [];

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

  it("lists with GET, adds with POST: CSRF header and user_id in the body", async () => {
    answer(200, { items: [] });
    await expect(listMyWords()).resolves.toEqual({ items: [] });
    await addMyWord(addBody({ word: "malak", meaning: "owner" }, "u1"));
    const body = { word: "malak", meaning: "owner", user_id: "u1" };
    expect(calls).toEqual([
      { url: "/api/me/words", method: "GET", csrf: null, body: undefined },
      { url: "/api/me/words", method: "POST", csrf: "kaagaz", body },
    ]);
  });

  it("keeps the 409's details, so the screen can ask", async () => {
    const details = { existing: [{ id: "w1", meaning: "boss" }] };
    answer(409, { error: { code: "word_has_meaning", message: "Asked.", details } });
    const failure = await addMyWord({}).catch((caught: unknown) => caught);
    expect(failure).toMatchObject({ status: 409, code: "word_has_meaning", details });
    expect(failedState(failure)).toEqual({ kind: "ask", existing: details.existing });
  });

  it("changes with PATCH (only what changed) and deletes with user_id in the query", async () => {
    answer(200, { word: word("w1", "malak", "owner"), warning: null });
    await changeMyWord("w1", { meaning: "owner", user_id: "u1" });
    answer(204);
    await deleteMyWord("w1", "u1");
    expect(calls).toEqual([
      { url: "/api/me/words/w1", method: "PATCH", csrf: "kaagaz", body: { meaning: "owner", user_id: "u1" } },
      { url: "/api/me/words/w1?user_id=u1", method: "DELETE", csrf: "kaagaz", body: undefined },
    ]);
  });
});

// Words are the user's text: never logged, never parsed as HTML.
describe("the my words sources", () => {
  const dir = join(__dirname, "..", "components", "words");
  const files = [...readdirSync(dir).map((name) => join(dir, name)), join(dir, "..", "chat", "AnswerView.tsx")];

  it("log nothing and set no HTML", () => {
    for (const file of files) expect(readFileSync(file, "utf8"), file).not.toMatch(/console\.|innerHTML/);
  });
});
