// Page turns, step 1 (#589): the turn menu's arithmetic, the pages laid out as turned, and each
// person's turns of a file kept in this browser under them and the file.

import { PluginRegistry } from "@embedpdf/core";
import type { PdfEngine, PdfPageObject } from "@embedpdf/models";
import { ScrollPlugin, ScrollPluginPackage, ScrollStrategy } from "@embedpdf/plugin-scroll";
import type { VirtualItem } from "@embedpdf/plugin-scroll";
import { ViewportPluginPackage } from "@embedpdf/plugin-viewport";
import { ZoomPluginPackage } from "@embedpdf/plugin-zoom";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  PageTurnsPlugin,
  PageTurnsPluginPackage,
  scrollTopKeepingPage,
  turnedSpreadPages,
  turnPages,
  turnPagesTo,
} from "@/components/extend/lib/page-turns";
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

// The engine's own scroll layout, fed the pages as PageTurnsPlugin hands them over: its private
// methods are called on a stand-in for the plugin, so a change inside the engine fails here.
type Strategy = {
  createVirtualItems(spreads: unknown): VirtualItem[];
  getScrollPositionForPage(page: number, items: VirtualItem[], scale: number, rotation: number): { y: number } | null;
  handleScroll(viewport: object, items: VirtualItem[], scale: number): { currentPage: number };
};
const engine = ScrollPlugin.prototype as unknown as {
  createStrategy(this: object, strategy: ScrollStrategy): Strategy;
  getSpreadPagesWithRotatedSize(this: object, documentId: string): unknown;
};
const GAP = 24;
function laidOut(filePages: readonly PdfPageObject[], pageTurns: PageTurns) {
  const plugin = {
    coreState: { core: { documents: { d: { document: { pages: filePages }, rotation: 0 } } } },
    spread: { forDocument: () => ({ getSpreadPages: () => turnedSpreadPages(filePages, pageTurns) }) },
    state: { defaultPageGap: GAP, defaultBufferSize: 2 },
    viewport: { getViewportGap: () => GAP },
  };
  const strategy = engine.createStrategy.call(plugin, ScrollStrategy.Vertical);
  return { strategy, items: strategy.createVirtualItems(engine.getSpreadPagesWithRotatedSize.call(plugin, "d")) };
}
const a4 = (count: number, rotation = 0) =>
  Array.from({ length: count }, (_, index) => ({ index, size: { width: 595, height: 842 }, rotation }) as PdfPageObject);
const every = (count: number, turn: number) => turns(Array.from({ length: count }, (_, i) => [i, turn]));
const view = (scrollTop: number) => ({ scrollTop, scrollLeft: 0, clientWidth: 900, clientHeight: 700 });

describe("the pages laid out as turned", () => {
  it("keeps the reader on their page through All pages: Turn right, on a 300-page file", () => {
    const pages = a4(300);
    const before = laidOut(pages, turns([]));
    const after = laidOut(pages, turnPages(turns([]), pages.keys(), 1));
    const scale = 1.25;
    for (const page of [100, 200]) {
      const item = before.items[page - 1]!;
      const scrollTop = before.strategy.getScrollPositionForPage(page, before.items, scale, 0)!.y + 0.2 * item.height * scale;
      expect(before.strategy.handleScroll(view(scrollTop), before.items, scale).currentPage).toBe(page);
      const kept = scrollTopKeepingPage(page, scrollTop, GAP, { ...before, scale }, { ...after, scale })!;
      expect(after.strategy.handleScroll(view(kept), after.items, scale).currentPage).toBe(page);
      expect(kept).toBeLessThan(scrollTop); // turned wide, the pages above are shorter
    }
  });

  it("keeps the page when the zoom changes too (a fit zoom fitting the wide pages)", () => {
    const pages = a4(300);
    const before = laidOut(pages, turns([]));
    const after = laidOut(pages, every(300, 1));
    const scrollTop = before.strategy.getScrollPositionForPage(150, before.items, 1, 0)!.y;
    const kept = scrollTopKeepingPage(150, scrollTop, GAP, { ...before, scale: 1 }, { ...after, scale: 0.7 })!;
    expect(after.strategy.handleScroll(view(kept), after.items, 0.7).currentPage).toBe(150);
  });

  it("lays a 200-page file opened with every page made wide out in order, and goes to each page's turned top", () => {
    const { strategy, items } = laidOut(a4(200), every(200, 1));
    const scale = 1.5;
    for (let n = 1; n <= 200; n += 1) {
      const item = items[n - 1]!;
      expect(item.height).toBe(595); // shown wide
      expect(item.offset).toBe((n - 1) * (595 + GAP));
      if (n > 1) expect(item.offset).toBeGreaterThan(items[n - 2]!.offset);
      expect(strategy.getScrollPositionForPage(n, items, scale, 0)!.y).toBe(GAP + item.offset * scale);
    }
  });

  it("is the plugin the engine's scroll and zoom plugins take the pages from", async () => {
    const registry = new PluginRegistry({} as PdfEngine);
    registry.registerPlugin(ViewportPluginPackage);
    registry.registerPlugin(ScrollPluginPackage);
    registry.registerPlugin(ZoomPluginPackage);
    registry.registerPlugin(PageTurnsPluginPackage);
    await registry.initialize();
    const pageTurns = registry.getPlugin<PageTurnsPlugin>("spread")!.provides();
    for (const id of ["scroll", "zoom"]) expect((registry.getPlugin(id) as unknown as { spread: unknown }).spread).toBe(pageTurns);
  });

  it("adds the person's turn to the file's own turn once, never twice", () => {
    // /Rotate 90 shows the A4 page wide; a person's quarter turn makes it tall again.
    const { items } = laidOut(a4(3, 1), turns([[1, 1]]));
    expect(items.map((item) => item.height)).toEqual([595, 842, 595]);
    expect(turnedSpreadPages(a4(3, 1), turns([[1, 1]])).map(([page]) => page!.rotation)).toEqual([1, 2, 1]);
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
