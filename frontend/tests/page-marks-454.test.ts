// Owner, 29 Oct 2026 (#454), part 2: while the PDF scrolled, the reading problems and my words of
// the page being left vanished the moment the next page became the screen's page (they were
// loaded for that one page only), and the next page's popped in after its request; and the
// low-resolution notice above the viewer came and went with the page, resizing the viewer, which
// then set its scroll again. Each page's marks are now kept once loaded, the pages beside the
// screen's page asked for ahead, once the page has held for SETTLE_MS (a fast scroll asks only
// for where it stops). usePageCache runs here with React's own act() in a bare root, as in
// upload-follow.test.ts: it renders nothing, so no DOM is needed.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SETTLE_MS, pagesAround, usePageCache } from "@/components/viewer/usePageCache";
import { useViewerWords } from "@/components/words/ViewerWords";
import type { ViewerWords } from "@/components/words/ViewerWords";
import { spotsLoadText } from "@/components/words/spots";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("the pages asked for", () => {
  it("are the page shown and those either side of it in a scroll, or the page alone", () => {
    expect(pagesAround(3, [1, 2, 3, 4, 5])).toEqual([3, 2, 4]);
    expect(pagesAround(1, [1, 2, 3])).toEqual([1, 2]);
    expect(pagesAround(3, [])).toEqual([3]); // a photo or a sheet: a page at a time, as before
    expect(pagesAround(null, [1, 2])).toEqual([]);
  });
});

describe("usePageCache", () => {
  let latest: ReadonlyMap<number, string> = new Map();
  let root: { render: (node: unknown) => void; unmount: () => void } | null = null;
  const load = vi.fn<(page: number) => Promise<string>>();

  function Probe({ version, pages }: { version: string; pages: number[] }) {
    latest = usePageCache(version, pages, load);
    return null;
  }
  async function show(version: string, pages: number[]) {
    if (!root) {
      const { createRoot } = await import("react-dom/client");
      const container = {
        nodeType: 1,
        tagName: "DIV",
        namespaceURI: null,
        textContent: "",
        ownerDocument: null,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      };
      root = createRoot(container as unknown as Element) as unknown as typeof root;
    }
    await act(async () => root!.render(createElement(Probe, { version, pages })));
  }
  const settle = (ms = SETTLE_MS) => act(async () => vi.advanceTimersByTime(ms));
  const asked = () => load.mock.calls.map(([page]) => page);

  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    const target = { addEventListener: () => undefined, removeEventListener: () => undefined };
    const page = { visibilityState: "visible", activeElement: null, ...target };
    vi.stubGlobal("document", page);
    vi.stubGlobal("window", { document: page, HTMLIFrameElement: class {}, ...target });
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    load.mockReset();
    load.mockImplementation(async (page) => `marks of page ${page}`);
  });
  afterEach(async () => {
    await act(async () => root?.unmount());
    root = null;
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps a page's marks once loaded, so leaving it never takes them off", async () => {
    await show("d1:0", [1, 2]);
    expect(asked()).toEqual([]); // nothing until the page has held
    await settle();
    expect(asked()).toEqual([1, 2]);
    expect([...latest.keys()]).toEqual([1, 2]);
    await show("d1:0", [2, 1, 3]); // scrolled on to page 2
    await settle();
    expect(asked()).toEqual([1, 2, 3]); // page 3 only: 1 and 2 are kept
    expect(latest.get(1)).toBe("marks of page 1");
    expect([...latest.keys()].sort()).toEqual([1, 2, 3]);
  });

  it("asks only for the pages a fast scroll stops on", async () => {
    for (const page of [1, 2, 3, 4, 5, 6, 7]) {
      await show("d1:0", [page]);
      await settle(SETTLE_MS - 50);
    }
    await settle();
    expect(asked()).toEqual([7]);
  });

  it("starts again after a reload, and asks again for a page whose load failed", async () => {
    load.mockImplementationOnce(async () => {
      throw new Error("offline");
    });
    await show("d1:0", [4]);
    await settle();
    expect(latest.has(4)).toBe(false);
    await show("d1:0", [5]);
    await show("d1:0", [4]);
    await settle();
    expect(latest.get(4)).toBe("marks of page 4");
    await show("d1:1", [4]); // a reload: the new version starts empty
    expect(latest.size).toBe(0);
    await settle();
    expect(asked()).toEqual([4, 4, 4]);
    expect(latest.get(4)).toBe("marks of page 4");
  });

  // Review panel, 29 Oct 2026: my words turned a failed load into an answer, so the page was
  // never asked again until Try again. It shows the failure, and asks again only once the page
  // is wanted again, never in a loop.
  it("shows a page's failed words, and asks for them again when that page is wanted again", async () => {
    let words: ViewerWords | null = null;
    function Words({ pageNo }: { pageNo: number }) {
      words = useViewerWords("d1", pageNo, [1, 2, 3, 4, 5, 6]);
      return null;
    }
    const pages: number[] = [];
    let fail = true;
    vi.stubGlobal("fetch", async (url: string) => {
      const page = Number(new URL(url, "http://x").searchParams.get("page"));
      pages.push(page);
      if (page === 4 && fail) {
        fail = false;
        return new Response(JSON.stringify({ error: { code: "error", message: "x" } }), { status: 500 });
      }
      return new Response(JSON.stringify({ items: [] }), { status: 200 });
    });
    const at = async (pageNo: number) => {
      await act(async () => root!.render(createElement(Words, { pageNo })));
      await settle();
    };
    await show("d1:0", []); // the bare root
    await at(4);
    expect(words!.error).toBe(spotsLoadText(500));
    await settle(SETTLE_MS * 10);
    expect(pages.filter((page) => page === 4)).toHaveLength(1); // not asked again while it stays
    await at(1);
    await at(4);
    expect(pages.filter((page) => page === 4)).toHaveLength(2);
    expect(words!.error).toBeNull();
  });
});

describe("the document screen's marks and notice (#454)", () => {
  const view = read("app/(app)/documents/[id]/DocumentView.tsx");
  const viewer = read("components/viewer/PdfDocumentViewer.tsx");

  it("gives the PDF viewer every loaded page's marks, and the panels the page's own", () => {
    const pdf = view.slice(view.indexOf("<PdfDocumentViewer"), view.indexOf("/>", view.indexOf("<PdfDocumentViewer")));
    expect(pdf).toContain("marks={words.markersAround}");
    expect(pdf).toContain("readingMarks={marksAround}");
    const pageViewer = view.slice(view.indexOf("<PageViewer"), view.indexOf("/>", view.indexOf("<PageViewer")));
    expect(pageViewer).toContain("marks={words.markers}");
    expect(pageViewer).toContain("readingMarks={readingMarks}");
    expect(view).toContain("marks={readingMarks}"); // the Needs attention panel, numbered per page as before
    expect(view).toContain("const readingMarks = (pageNo !== null && marksByPage.get(pageNo)) || NO_MARKS;");
    expect(view).toContain('const scroll = shown?.detected_type === "pdf" ? shown.pages.map((p) => p.page_no) : [];');
  });

  it("lights a hot mark (DD7) only on the screen's page, as the panel numbers them", () => {
    expect(viewer).toContain("const here = pageNumber === pageNo;");
    expect(viewer).toContain('data-hot={here && hotMark === index ? "true" : undefined}');
    expect(viewer).toContain("onPointerEnter={() => (here ? onHotMark?.(index) : undefined)}");
  });

  it("keeps the low-resolution notice's room on every page of a PDF that has one", () => {
    expect(view).toContain("const lowRoom = detail.detected_type === \"pdf\" ? (detail.pages.map(lowNote).find((note) => note !== null) ?? null) : null;");
    expect(view).toMatch(/\{\(lowResolution \?\? lowRoom\) && \(\s*<p className=\{styles\.trashNotice\} role="note" style=\{lowResolution \? undefined : \{ visibility: "hidden" \}\}>/);  });
});
