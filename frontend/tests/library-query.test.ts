import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ALL_DOCUMENTS,
  MAX_TAG_FILTERS,
  dayAfter,
  includeSubfoldersHref,
  isFiltered,
  isIsoDate,
  libraryFromSearch,
  libraryHref,
  listParams,
} from "@/components/documents/libraryQuery";
import type { LibraryState } from "@/components/documents/libraryQuery";
import { attentionWords } from "@/components/documents/library";
import { bulkDoneMessage, bulkFailedMessage, bulkReadQuestion } from "@/components/documents/trash";
import { listDocuments } from "@/lib/api";

const FOLDER = "0b4f7a8e-1c2d-4e5f-8a9b-0c1d2e3f4a5b";
const GST = "5d6e7f80-9a1b-4c2d-8e3f-4a5b6c7d8e9f";
const PAID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listDocuments", () => {
  it("sends every filter: the folder, subfolders, each tag, the type and the dates", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    });
    await listDocuments({
      limit: 25,
      offset: 0,
      folder_id: FOLDER,
      include_subfolders: true,
      tag_id: [GST, PAID],
      detected_type: "pdf",
      uploaded_from: "2026-09-01",
      uploaded_to: "2026-10-01",
    });
    expect(urls).toEqual([
      `/api/documents?limit=25&offset=0&folder_id=${FOLDER}&include_subfolders=true` +
        `&tag_id=${GST}&tag_id=${PAID}&detected_type=pdf` +
        "&uploaded_from=2026-09-01&uploaded_to=2026-10-01",
    ]);
  });
});

describe("dates", () => {
  it("knows a real calendar date written as YYYY-MM-DD", () => {
    expect(isIsoDate("2026-09-30")).toBe(true);
    expect(isIsoDate("2028-02-29")).toBe(true);
    expect(isIsoDate("2026-02-29")).toBe(false);
    expect(isIsoDate("2026-09-31")).toBe(false);
    expect(isIsoDate("2026-9-3")).toBe(false);
    expect(isIsoDate("30/09/2026")).toBe(false);
    expect(isIsoDate("")).toBe(false);
  });

  it("gives the next day across a month, a year and a leap day", () => {
    expect(dayAfter("2026-09-29")).toBe("2026-09-30");
    expect(dayAfter("2026-09-30")).toBe("2026-10-01");
    expect(dayAfter("2026-12-31")).toBe("2027-01-01");
    expect(dayAfter("2028-02-28")).toBe("2028-02-29");
  });
});

describe("the library in the page address", () => {
  const everything: LibraryState = {
    folderId: FOLDER,
    subfolders: true,
    trash: false,
    tagIds: [GST, PAID],
    type: "pdf",
    from: "2026-09-01",
    to: "2026-09-30",
    attention: true,
  };

  it("is plain /documents for all documents with no filter", () => {
    expect(libraryHref(ALL_DOCUMENTS)).toBe("/documents");
    expect(libraryFromSearch(new URLSearchParams(""))).toEqual(ALL_DOCUMENTS);
  });

  it("writes the folder and filters, and reads them back the same", () => {
    const href = libraryHref(everything);
    expect(href).toBe(
      `/documents?folder=${FOLDER}&sub=1&tag=${GST}&tag=${PAID}&type=pdf&from=2026-09-01&to=2026-09-30&attention=1`,
    );
    expect(libraryFromSearch(new URLSearchParams(href.split("?")[1]))).toEqual(everything);
  });

  it("ignores what it can't use: bad ids, unknown types, impossible dates, subfolders without a folder", () => {
    const state = libraryFromSearch(
      new URLSearchParams("folder=../admin&sub=1&tag=x&tag=1&type=exe&from=2026-02-30&to=soon"),
    );
    expect(state).toEqual(ALL_DOCUMENTS);
  });

  it("keeps each tag once, in lower case, and at most 50", () => {
    const many = Array.from(
      { length: 60 },
      (_, n) => `tag=00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
    );
    const state = libraryFromSearch(
      new URLSearchParams([`tag=${GST.toUpperCase()}`, `tag=${GST}`, ...many].join("&")),
    );
    expect(state.tagIds[0]).toBe(GST);
    expect(new Set(state.tagIds).size).toBe(state.tagIds.length);
    expect(state.tagIds).toHaveLength(MAX_TAG_FILTERS);
  });

  it("has Trash as its own view, with no folder, and keeps the filters there", () => {
    const trash: LibraryState = { ...ALL_DOCUMENTS, trash: true, tagIds: [GST] };
    expect(libraryHref(trash)).toBe(`/documents?trash=1&tag=${GST}`);
    expect(libraryFromSearch(new URLSearchParams(`trash=1&tag=${GST}`))).toEqual(trash);
    expect(
      libraryFromSearch(new URLSearchParams(`trash=1&folder=${FOLDER}&sub=1`)),
    ).toEqual({ ...ALL_DOCUMENTS, trash: true });
    expect(libraryFromSearch(new URLSearchParams("trash=yes")).trash).toBe(false);
  });
});

describe("listParams", () => {
  it("asks only for what is in Trash in the Trash view", () => {
    expect(listParams({ ...ALL_DOCUMENTS, trash: true }, "", 0)).toEqual({
      limit: 25,
      offset: 0,
      trashed: true,
    });
  });

  it("asks for a page of everything when nothing is chosen", () => {
    expect(listParams(ALL_DOCUMENTS, "", 0)).toEqual({ limit: 25, offset: 0 });
  });

  it("sends the folder, and always says whether subfolders are included", () => {
    expect(listParams({ ...ALL_DOCUMENTS, folderId: FOLDER }, "", 25)).toEqual({
      limit: 25,
      offset: 25,
      folder_id: FOLDER,
      include_subfolders: false,
    });
  });

  it("sends the tags, the type and the search words", () => {
    expect(
      listParams({ ...ALL_DOCUMENTS, tagIds: [GST], type: "text" }, "march", 0),
    ).toEqual({ limit: 25, offset: 0, q: "march", tag_id: [GST], detected_type: "text" });
  });

  it("turns the last day chosen into the exclusive end the API expects", () => {
    expect(
      listParams({ ...ALL_DOCUMENTS, from: "2026-09-01", to: "2026-09-30" }, "", 0),
    ).toEqual({ limit: 25, offset: 0, uploaded_from: "2026-09-01", uploaded_to: "2026-10-01" });
  });
});

describe("isFiltered", () => {
  it("counts tags, type and dates, but not the folder", () => {
    expect(isFiltered(ALL_DOCUMENTS)).toBe(false);
    expect(isFiltered({ ...ALL_DOCUMENTS, folderId: FOLDER, subfolders: true })).toBe(false);
    expect(isFiltered({ ...ALL_DOCUMENTS, tagIds: [GST] })).toBe(true);
    expect(isFiltered({ ...ALL_DOCUMENTS, type: "pdf" })).toBe(true);
    expect(isFiltered({ ...ALL_DOCUMENTS, to: "2026-09-30" })).toBe(true);
  });
});

describe("includeSubfoldersHref (QA, 15 Oct 2026)", () => {
  it("offers the subfolders from an empty folder that has some, and only then", () => {
    const folder = { ...ALL_DOCUMENTS, folderId: FOLDER };
    expect(includeSubfoldersHref(folder, 2)).toBe(`/documents?folder=${FOLDER}&sub=1`);
    expect(includeSubfoldersHref(folder, 0)).toBeNull();
    expect(includeSubfoldersHref({ ...folder, subfolders: true }, 2)).toBeNull();
    expect(includeSubfoldersHref(ALL_DOCUMENTS, 2)).toBeNull();
  });
});

describe("Needs attention (PRD 5 U2)", () => {
  it("is a filter in the page address, never in Trash, and sent as attention=true", async () => {
    const attention: LibraryState = { ...ALL_DOCUMENTS, attention: true };
    expect(libraryHref(attention)).toBe("/documents?attention=1");
    expect(libraryFromSearch(new URLSearchParams("attention=1"))).toEqual(attention);
    expect(libraryFromSearch(new URLSearchParams("attention=yes")).attention).toBe(false);
    expect(libraryFromSearch(new URLSearchParams("trash=1&attention=1")).attention).toBe(false);
    expect(isFiltered(attention)).toBe(true);
    expect(listParams(attention, "", 0)).toEqual({ limit: 25, offset: 0, attention: true });
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    });
    await listDocuments(listParams(attention, "", 0));
    expect(urls).toEqual(["/api/documents?limit=25&offset=0&attention=true"]);
  });

  it("puts a chip on a row with problems, in the PRD's words, and none on a clean one", () => {
    const none = { pages_unreadable: 0, pages_uncertain: 0, values_to_review: 0, words_unsure: 0 };
    expect(attentionWords(none)).toEqual([]);
    expect(attentionWords({ ...none, pages_unreadable: 2, pages_uncertain: 1, words_unsure: 14, values_to_review: 3 })).toEqual([
      "2 pages unreadable",
      "14 words unsure",
      "3 values to check",
    ]);
    expect(attentionWords({ ...none, pages_unreadable: 1, words_unsure: 1, values_to_review: 1 })).toEqual([
      "1 page unreadable",
      "1 word unsure",
      "1 value to check",
    ]);
    // An uncertain page with no unsure word (a spot not confirmed), or a reply from before words_unsure.
    expect(attentionWords({ pages_unreadable: 0, pages_uncertain: 2, values_to_review: 0 })).toEqual(["2 pages to check"]);
  });

  it("asks before a selection is read again, then says what happened", () => {
    expect(bulkReadQuestion(3)).toBe(
      "Read 3 documents again? Reviewer fixes come back only if they were saved as remembered fixes, and each one is out of search until it is read.",
    );
    expect(bulkDoneMessage("read", 1)).toBe("Reading 1 document again.");
    expect(bulkFailedMessage("read", 2, "Try later.")).toBe("2 documents could not be read again. Try later.");
  });
});
