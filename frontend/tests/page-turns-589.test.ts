// Page turns, step 1 (#589): the turn menu's arithmetic, and each person's turns of a file kept
// in this browser under them and the file.

import { afterEach, describe, expect, it, vi } from "vitest";
import { turnPages, turnPagesTo } from "@/components/extend/lib/page-turns";
import type { PageTurns } from "@/components/extend/lib/page-turns";
import { pageTurnsKey, readPageTurns, savePageTurns } from "@/components/viewer/viewer";

const turns = (entries: [number, number][]) => new Map(entries) as PageTurns;
const tall = { size: { width: 595, height: 842 }, rotation: 0 }; // A4 upright
const wide = { size: { width: 842, height: 595 }, rotation: 0 }; // A4 on its side
const tallTurnedInFile = { size: { width: 595, height: 842 }, rotation: 1 }; // /Rotate 90: shown wide
const square = { size: { width: 600, height: 600 }, rotation: 0 };
const pages = [tall, wide, tallTurnedInFile, square];

describe("the turn menu", () => {
  it("turns one page left and right, and four quarter turns bring it back", () => {
    expect([...turnPages(turns([]), [1], -1)]).toEqual([[1, 3]]);
    expect([...turnPages(turns([]), [1], 1)]).toEqual([[1, 1]]);
    let now = turns([]);
    for (let i = 0; i < 4; i += 1) now = turnPages(now, [1], 1);
    expect(now.size).toBe(0);
  });

  it("turns every page a quarter on top of its own turn, so a page turned by itself keeps its extra turn", () => {
    const all = turnPages(turns([[2, 1]]), [0, 1, 2, 3], 1);
    expect(all).toEqual(new Map([[0, 1], [1, 1], [2, 2], [3, 1]]));
    expect(turnPages(all, [0, 1, 2, 3], -1)).toEqual(new Map([[2, 1]]));
  });

  it("makes all pages wide by turning only the tall ones, by their shape as shown", () => {
    // Page 0 is tall; page 1 wide; page 2 tall but shown wide by its file's own turn; page 3 square.
    expect(turnPagesTo(turns([]), pages, "wide")).toEqual(new Map([[0, 1]]));
    // Page 1 turned by the person is shown tall, so it is turned again.
    expect(turnPagesTo(turns([[1, 1]]), pages, "wide")).toEqual(new Map([[0, 1], [1, 2]]));
  });

  it("makes all pages tall by turning only the wide ones", () => {
    expect(turnPagesTo(turns([]), pages, "tall")).toEqual(new Map([[1, 1], [2, 1]]));
    // Page 0 turned upside down is still tall: left as it is.
    expect(turnPagesTo(turns([[0, 2]]), pages, "tall")).toEqual(new Map([[0, 2], [1, 1], [2, 1]]));
  });

  it("does nothing to pages already the shape asked for", () => {
    const wideAlready = turnPagesTo(turns([]), pages, "wide");
    expect(turnPagesTo(wideAlready, pages, "wide")).toEqual(wideAlready);
  });
});

describe("the turns kept in this browser", () => {
  afterEach(() => vi.unstubAllGlobals());
  const fakeStore = () => {
    const data = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => data.set(k, v),
      removeItem: (k: string) => data.delete(k),
    });
    return data;
  };

  it("keys them by the signed-in person and the file's version", () => {
    expect(pageTurnsKey("u1", "d1")).toBe("kaagaz.page-turns:u1:d1");
  });

  it("gives the turns back to the same person on the same file, and to no one else", () => {
    fakeStore();
    savePageTurns("u1", "d1", new Map([[0, 1], [4, 3]]));
    expect(readPageTurns("u1", "d1")).toEqual(new Map([[0, 1], [4, 3]]));
    expect(readPageTurns("u2", "d1").size).toBe(0); // another person on this browser
    expect(readPageTurns("u1", "d2").size).toBe(0); // another file, or a new version of it
  });

  it("forgets them on Reset to original", () => {
    const data = fakeStore();
    savePageTurns("u1", "d1", new Map([[0, 1]]));
    savePageTurns("u1", "d1", new Map());
    expect(data.size).toBe(0);
    expect(readPageTurns("u1", "d1").size).toBe(0);
  });

  it("drops anything that is not a page and a quarter turn", () => {
    const data = fakeStore();
    data.set(pageTurnsKey("u1", "d1"), JSON.stringify({ 0: 2, 1: 4, "-1": 1, x: 1, 2: "1", 3: 1.5 }));
    expect(readPageTurns("u1", "d1")).toEqual(new Map([[0, 2]]));
    data.set(pageTurnsKey("u1", "d1"), "not json");
    expect(readPageTurns("u1", "d1").size).toBe(0);
    data.set(pageTurnsKey("u1", "d1"), "null");
    expect(readPageTurns("u1", "d1").size).toBe(0);
  });

  it("never throws when storage is missing, blocked or full", () => {
    expect(readPageTurns("u1", "d1").size).toBe(0); // no storage in node
    expect(() => savePageTurns("u1", "d1", new Map([[0, 1]]))).not.toThrow();
    const refuse = () => {
      throw new Error("QuotaExceededError");
    };
    vi.stubGlobal("localStorage", { getItem: refuse, setItem: refuse, removeItem: refuse });
    expect(readPageTurns("u1", "d1").size).toBe(0);
    expect(() => savePageTurns("u1", "d1", new Map([[0, 1]]))).not.toThrow();
    expect(() => savePageTurns("u1", "d1", new Map())).not.toThrow();
  });
});
