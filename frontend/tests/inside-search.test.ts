import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DEFAULT_SEARCH_MODE,
  SEARCH_MODES,
  noResultsText,
  pagesText,
  resultView,
  searchModeFor,
  searchParams,
} from "@/components/documents/insideSearch";
import { ALL_DOCUMENTS, listParams } from "@/components/documents/libraryQuery";
import type { LibraryState } from "@/components/documents/libraryQuery";
import { searchDocuments } from "@/lib/api";
import { decodeRegion } from "@/lib/regions";
import type { FoundDocument } from "@/lib/types";

const FOLDER = "0b4f7a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b";
const GST = "5d6e7f80-9a1b-4c2d-8e3f-4a5b6c7d8e9f";
const DOC = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";
const FILTERED: LibraryState = {
  ...ALL_DOCUMENTS,
  folderId: FOLDER,
  subfolders: true,
  tagIds: [GST],
  type: "pdf",
  from: "2026-09-01",
  to: "2026-09-30",
};

function found(overrides: Partial<FoundDocument> = {}): FoundDocument {
  return {
    id: DOC,
    display_name: "patil-bill.pdf",
    doc_type: "invoice",
    score: 0.03,
    best: {
      snippet: "Grand Total: Rs. 1,20,000",
      page_from: 15,
      page_to: 16,
      regions: [
        { kind: "box", page: 16, box: [0.1, 0.1, 0.5, 0.2] },
        { kind: "box", page: 15, box: [0.1, 0.8, 0.9, 0.9] },
      ],
    },
    more: 2,
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the search mode", () => {
  it("is inside documents by default, with names as the other choice", () => {
    expect(DEFAULT_SEARCH_MODE).toBe("inside");
    expect(SEARCH_MODES.map((choice) => choice.label)).toEqual(["Inside documents", "Names"]);
    expect(searchModeFor("inside", ALL_DOCUMENTS)).toBe("inside");
  });

  it("is names only in Trash, which search leaves out", () => {
    expect(searchModeFor("inside", { ...ALL_DOCUMENTS, trash: true })).toBe("names");
  });
});

describe("the requests of both modes", () => {
  it("inside documents: the question, the folder, subfolders, tags and type, never the upload dates", () => {
    expect(searchParams(FILTERED, "patil")).toEqual({
      q: "patil",
      folder_id: FOLDER,
      include_subfolders: true,
      tag_ids: [GST],
      detected_type: "pdf",
    });
    expect(searchParams(ALL_DOCUMENTS, "patil")).toEqual({ q: "patil" });
  });

  it("names: the list's q with the same filters (the list keeps its dates)", () => {
    const names = listParams(FILTERED, "patil", 0);
    expect(names).toMatchObject({ q: "patil", folder_id: FOLDER, tag_id: [GST], detected_type: "pdf" });
    expect(listParams(FILTERED, "", 0).q).toBeUndefined(); // inside documents: the list is not narrowed by name
  });

  it("sends GET /api/search with each tag as its own key", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify({ documents: [] }), { status: 200 });
    });
    await searchDocuments(searchParams(FILTERED, "पाटील bill"));
    expect(urls).toEqual([
      `/api/search?q=${encodeURIComponent("पाटील").replace(/%20/g, "+")}+bill&folder_id=${FOLDER}` +
        `&include_subfolders=true&tag_ids=${GST}&detected_type=pdf`,
    ]);
  });
});

describe("a result", () => {
  it("says Page 15, or Pages 15 to 16 across two pages", () => {
    expect(pagesText(15, 15)).toBe("Page 15");
    expect(pagesText(15, 16)).toBe("Pages 15 to 16");
  });

  it("shows the name, type, snippet and pages, and opens the viewer on its first page's spot", () => {
    const view = resultView(found());
    expect(view).toMatchObject({
      id: DOC,
      name: "patil-bill.pdf",
      type: "Invoice",
      snippet: "Grand Total: Rs. 1,20,000",
      pages: "Pages 15 to 16",
    });
    const link = new URL(view.href, "https://kaagaz.test");
    expect(link.pathname).toBe(`/documents/${DOC}`);
    expect(link.searchParams.get("page")).toBe("15");
    expect(decodeRegion(link.searchParams.get("region"))).toEqual(found().best.regions[1]);
  });

  it("opens the page itself when the piece has no region, and says Unknown for no type", () => {
    const view = resultView(found({ doc_type: null, best: { ...found().best, regions: [] } }));
    expect(view.type).toBe("Unknown");
    expect(view.href).toBe(`/documents/${DOC}?page=15`);
  });

  it("keeps markup in a name or a snippet as plain text, and the link stays on this site", () => {
    const payload = '<img src=x onerror=alert(1)><a href="javascript:alert(1)">x</a>';
    const view = resultView(
      found({ display_name: payload, best: { ...found().best, snippet: payload } }),
    );
    expect(view.name).toBe(payload); // React shows it as characters; nothing parses it
    expect(view.snippet).toBe(payload);
    expect(view.href.startsWith(`/documents/${DOC}?`)).toBe(true);
  });

  it("says nothing was found, here when a folder, tag or type narrows the search", () => {
    expect(noResultsText(ALL_DOCUMENTS)).toBe("No document holds these words.");
    expect(noResultsText({ ...ALL_DOCUMENTS, tagIds: [GST] })).toBe("No document here holds these words.");
    expect(noResultsText({ ...ALL_DOCUMENTS, from: "2026-09-01" })).toBe("No document holds these words.");
  });
});
