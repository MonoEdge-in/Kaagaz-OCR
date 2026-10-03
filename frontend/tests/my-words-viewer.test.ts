import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { browserStore, markedCells, markersOf, markerRect, markerText, pickableWords } from "@/components/words/spots";
import { readShowMyWords, SHOW_KEY, spotRegion, spotsLoadText, viewerBody, wordFromText } from "@/components/words/spots";
import { writeShowMyWords } from "@/components/words/spots";
import { failedState, retryBody, savedState } from "@/components/words/words";
import { addMyWord, ApiError, getMyWordsOnPage } from "@/lib/api";
import type { MySpot, Region } from "@/lib/types";

const box = (page: number, b: number[]) => ({ kind: "box", page, box: b }) as Region;
const cell = (page: number, row: number, column: number): Region => ({ kind: "cell", page, row, column });
const spot = (id: string, region: Region, word = "malak", meaning = "owner"): MySpot =>
  ({ id, word_id: `w-${id}`, word, meaning, page_no: region.page, region });

describe("the spot's region, as the server takes it", () => {
  it("keeps a box inside the page with its corners in order", () => {
    expect(spotRegion(box(2, [0.1, 0.2, 0.3, 0.4]))).toEqual(box(2, [0.1, 0.2, 0.3, 0.4]));
    expect(spotRegion(box(1, [0.3, 0.4, 0.1, 0.2]))).toEqual(box(1, [0.1, 0.2, 0.3, 0.4]));
    expect(spotRegion(box(1, [-0.01, 0.5, 1.02, 0.6]))).toEqual(box(1, [0, 0.5, 1, 0.6])); // a reader's rounding
  });

  it("refuses a box with no size, odd numbers or the wrong shape", () => {
    for (const b of [[0.2, 0.2, 0.2, 0.4], [1.1, 0.1, 1.5, 0.2], [0, 0, NaN, 1], [0, 0, Infinity, 1], [0, 0, 1], ["0", 0, 1, 1]]) {
      expect(spotRegion(box(1, b as number[])), JSON.stringify(b)).toBeNull();
    }
  });

  it("takes a cell's row and column from 1 to 10,000 only", () => {
    expect(spotRegion(cell(3, 1, 10_000))).toEqual(cell(3, 1, 10_000));
    for (const [row, column] of [[0, 1], [1, 10_001], [1.5, 2], [-1, 2]] as const) expect(spotRegion(cell(1, row, column))).toBeNull();
  });

  it("refuses a page under 1, another kind, extra keys aside, and anything not a region", () => {
    expect(spotRegion(box(0, [0, 0, 1, 1]))).toBeNull();
    expect(spotRegion({ kind: "line", page: 1 })).toBeNull();
    expect(spotRegion({ ...cell(1, 2, 3), extra: "<b>" })).toEqual(cell(1, 2, 3)); // only the known keys are sent
    for (const odd of [null, undefined, "box", 7]) expect(spotRegion(odd)).toBeNull();
  });
});

describe("the POST for a word on a spot", () => {
  it("is B1's body plus the spot, on the region's page", () => {
    const body = viewerBody({ word: "  malak ", meaning: "owner" }, "u1", "d1", box(2, [0.4, 0.2, 0.1, 0.3]));
    expect(body).toEqual({
      word: "malak",
      meaning: "owner",
      user_id: "u1",
      spot: { document_id: "d1", page_no: 2, region: box(2, [0.1, 0.2, 0.4, 0.3]) },
    });
    expect(viewerBody({ word: "a", meaning: "b" }, "u1", "d1", cell(5, 7, 2))?.spot).toEqual(
      { document_id: "d1", page_no: 5, region: cell(5, 7, 2) },
    );
  });

  it("is null for a spot the server would refuse", () => {
    expect(viewerBody({ word: "a", meaning: "b" }, "u1", "d1", cell(1, 0, 1))).toBeNull();
  });

  it("keeps the spot when the duplicate question is answered", () => {
    const body = viewerBody({ word: "malak", meaning: "owner" }, "u1", "d1", cell(1, 2, 3));
    if (!body) throw new Error("no body");
    expect(retryBody(body, { kind: "add_too" })).toEqual({ ...body, confirm_another: true });
    expect(retryBody(body, { kind: "replace", id: "w9" })).toEqual({ ...body, replace_id: "w9" });
    expect(retryBody(body, { kind: "cancel" })).toBeNull();
  });
});

describe("the word, prefilled as read", () => {
  it("is the whole value or line when it can be a word", () => {
    expect(wordFromText("  saat   bara ")).toBe("saat bara");
    expect(wordFromText("बोजा")).toBe("बोजा");
  });

  it("is the first word that can be one, else empty", () => {
    expect(wordFromText("Malak: Ramesh Patil, Gat 33")).toBe("Malak");
    expect(wordFromText("12,500.00 / 33")).toBe("");
  });

  it("offers each word of a line once, without the punctuation at its ends", () => {
    expect(pickableWords("Malak: (malak) Ramesh, 7/12 gat-33 <b>")).toEqual(["Malak", "Ramesh", "gat-33", "b"]);
    expect(pickableWords("ab⟦U+202E⟧cd ok")).toEqual(["ok"]); // a hidden character shown as its code is not a word
    expect(pickableWords(Array.from({ length: 20 }, (_, n) => `w${n}`).join(" "))).toHaveLength(12);
  });
});

describe("what a save says, by status", () => {
  it("says it saved, and asks on 409 word_has_meaning", () => {
    const word = { id: "w1", word: "malak", meaning: "owner", source: "viewer", built_in: false, created_at: "", updated_at: "" } as const;
    expect(savedState({ word, outcome: "created", warning: null }).message).toBe("Saved 'malak' as 'owner'.");
    const details = { existing: [{ id: "w0", meaning: "boss" }] };
    expect(failedState(new ApiError(409, "word_has_meaning", "x", details))).toEqual({ kind: "ask", existing: details.existing });
  });

  it("gives the server's words for 400, 409 limits and 429, and its own for 404 and no answer", () => {
    for (const [status, code] of [[400, "bad_request"], [409, "too_many_meanings"], [429, "rate_limited"]] as const) {
      expect(failedState(new ApiError(status, code, `said ${code}`))).toEqual({ kind: "refused", message: `said ${code}` });
    }
    expect(failedState(new ApiError(404, "not_found", "x"))).toEqual({ kind: "refused", message: "That word or file isn't there any more. Reload the page." });
    expect(failedState(new ApiError(0, "network", "x")).kind).toBe("refused");
  });

  it("says why this page's words did not load", () => {
    expect(spotsLoadText(429)).toMatch(/^Too many requests/);
    expect(spotsLoadText(404)).toBe("This document isn't there any more.");
    expect(spotsLoadText(500)).toBe("Your words on this page could not be loaded. Try again.");
    expect(spotsLoadText(null)).toMatch(/Check your internet connection/);
  });
});

describe("the markers", () => {
  const spots = [
    spot("s1", box(2, [0.1, 0.1, 0.2, 0.15])),
    spot("s2", box(3, [0.1, 0.1, 0.2, 0.15])),
    spot("s3", box(2, [0.5, 0.5, 0.5, 0.6])),
    spot("s4", cell(2, 4, 2), "बोजा", "mortgage charge"),
  ];

  it("numbers this page's spots in the server's order, leaving out other pages and bad regions", () => {
    const markers = markersOf(spots, 2);
    expect(markers.map((m) => [m.id, m.n])).toEqual([["s1", 1], ["s4", 2]]);
    expect(markerText(markers[1]!)).toBe("'बोजा' means 'mortgage charge'");
  });

  it("puts a box on the drawn page, never smaller than 12 px, and keeps it on the page", () => {
    const rect = markerRect([0.1, 0.2, 0.5, 0.4], 1000, 500);
    for (const [key, px] of Object.entries({ left: 100, top: 100, width: 400, height: 100 })) {
      expect(rect[key as keyof typeof rect]).toBeCloseTo(px, 6);
    }
    expect(markerRect([0.5, 0.25, 0.5, 0.25], 1000, 1000)).toEqual({ left: 494, top: 244, width: 12, height: 12 });
    expect(markerRect([0, 0, 0.001, 0.001], 1000, 1000)).toEqual({ left: 0, top: 0, width: 12, height: 12 });
    expect(markerRect([0.999, 0.999, 1, 1], 1000, 1000)).toEqual({ left: 988, top: 988, width: 12, height: 12 });
    expect(markerRect([0, 0, 1, 1], 8, 8)).toEqual({ left: 0, top: 0, width: 8, height: 8 });
  });

  it("groups cell markers by row and column", () => {
    const markers = markersOf([spot("a", cell(1, 4, 2)), spot("b", cell(1, 4, 2)), spot("c", box(1, [0, 0, 1, 1]))], 1);
    expect([...markedCells(markers)].map(([key, list]) => [key, list.map((m) => m.n)])).toEqual([["4:2", [1, 2]]]);
  });
});

describe("Show my words, remembered in this browser", () => {
  const store = (seed: Record<string, string> = {}) => {
    const data = new Map(Object.entries(seed));
    return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v), data };
  };
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("full"); } };

  it("is on by default, off only when turned off here", () => {
    expect(readShowMyWords(store())).toBe(true);
    expect(readShowMyWords(store({ [SHOW_KEY]: "0" }))).toBe(false);
    expect(readShowMyWords(store({ [SHOW_KEY]: "1" }))).toBe(true);
    expect(readShowMyWords(null)).toBe(true);
  });

  it("writes 0 or 1", () => {
    const s = store();
    writeShowMyWords(s, false);
    expect(s.data.get(SHOW_KEY)).toBe("0");
    writeShowMyWords(s, true);
    expect(s.data.get(SHOW_KEY)).toBe("1");
  });

  it("never throws when storage does", () => {
    expect(readShowMyWords(broken)).toBe(true);
    expect(() => writeShowMyWords(broken, false)).not.toThrow();
    expect(() => writeShowMyWords(null, false)).not.toThrow();
    expect(browserStore()).toBeNull(); // no window in node
    vi.stubGlobal("window", { get localStorage(): Storage { throw new Error("SecurityError"); } });
    expect(browserStore()).toBeNull();
    vi.unstubAllGlobals();
  });
});

describe("the viewer's calls", () => {
  const calls: { url: string; method: string; body: unknown }[] = [];
  afterEach(() => {
    vi.unstubAllGlobals();
    calls.length = 0;
  });

  it("asks for one page's words with GET, and saves a spot with POST", async () => {
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    });
    await getMyWordsOnPage("d/1", 3);
    const body = viewerBody({ word: "malak", meaning: "owner" }, "u1", "d1", cell(1, 2, 3));
    await addMyWord(body ?? {});
    expect(calls).toEqual([
      { url: "/api/documents/d%2F1/my-words?page=3", method: "GET", body: undefined },
      { url: "/api/me/words", method: "POST", body },
    ]);
  });
});

// The document's text and the user's words: never logged, never parsed as HTML.
describe("the viewer sources", () => {
  const root = join(__dirname, "..");
  const files = ["app/(app)/documents/[id]/DocumentView.tsx", "components/viewer/ScannedTextPanel.tsx",
    "components/viewer/PageViewer.tsx", "components/viewer/SheetGrid.tsx"];

  it("log nothing and set no HTML", () => {
    for (const file of files) expect(readFileSync(join(root, file), "utf8"), file).not.toMatch(/console\.|innerHTML/);
  });
});
