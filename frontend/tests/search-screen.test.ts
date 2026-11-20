// The Search screen (components/search/SearchScreen.tsx; docs/architecture.md, The screens).
// Changed on purpose (#730, Tushar 20 Nov 2026): the top bar's search finds a file or folder by
// name, by a path pasted in any common form (pathSearch.ts) or by a Kaagaz file link; searching
// inside documents is the Documents screen's own search now, and old ?in= links search their words
// here. Also "Copy path" and "Copy link" (copyPath.ts), whose path Search reads back.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { buildTree } from "@/components/documents/folderTree";
import {
  COPY_FAILED,
  LINK_COPIED,
  PATH_COPIED,
  copyFor,
  detailPath,
  fileLink,
  filePath,
} from "@/components/documents/copyPath";
import { menuEntries } from "@/components/documents/library";
import { NO_MATCH_TEXT, documentFromLink, findByPath, pathParts } from "@/components/search/pathSearch";
import { SearchResults, runSearch, searchFromAddress, searchHref } from "@/components/search/SearchScreen";
import { ApiError } from "@/lib/api";
import type { DocumentDetail, DocumentSummary, Folder } from "@/lib/types";

const api = vi.hoisted(() => ({ getDocumentSummary: vi.fn(), listFolders: vi.fn(), listDocuments: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), ...api }));
vi.mock("next/link", async () => {
  const { createElement: make } = await import("react");
  return { default: ({ href, children }: { href: string; children: unknown }) => make("a", { href }, children as never) };
});

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const ID = "0b6f2a4e-1c2d-4e5f-8a9b-0c1d2e3f4a5b";

// What GET /folders gives this person: Finance is hidden from them, so Salaries (inside it) comes
// with no parent, and its name is never anywhere here.
const folders: Folder[] = [
  { id: "d1", name: "Land & Legal", parent_id: null, created_at: "2026-10-01", kind: "department" },
  { id: "f2", name: "Land records", parent_id: "d1", created_at: "2026-10-01" },
  { id: "f3", name: "Survey 44, Wakad", parent_id: "f2", created_at: "2026-10-01" },
  { id: "f4", name: "Sale deeds", parent_id: "d1", created_at: "2026-10-01" },
  { id: "sal", name: "Salaries", parent_id: null, created_at: "2026-10-01" },
];
const tree = buildTree(folders);
const file = (id: string, name: string, folder_id: string | null, more: Partial<DocumentSummary> = {}) =>
  ({ id, display_name: name, folder_id, shared_with_you: false, status: "ready", detected_type: "pdf", failure_code: null, ...more }) as DocumentSummary;
const extract = file("x1", "7-12 Extract.pdf", "f3");
const older = file("x2", "7-12 Extract old.pdf", "f4");
const slip = file("x3", "Pay slip.pdf", "sal");
const map = file("x4", "Survey 44 map.pdf", "f3");
const named = [extract, older, slip, map];
const ids = (text: string) => findByPath(tree, pathParts(text), named).files.map((found) => found.id);

afterEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
});

describe("the search's address", () => {
  it("reads the words, and an old inside or names link searches the same words", () => {
    expect(searchFromAddress(new URLSearchParams("q=rent"))).toEqual({ q: "rent" });
    expect(searchFromAddress(new URLSearchParams("q=rent&in=inside"))).toEqual({ q: "rent" });
    expect(searchFromAddress(new URLSearchParams("q=+Mehta+&in=names"))).toEqual({ q: "Mehta" });
    expect(searchFromAddress(new URLSearchParams(`q=${"a".repeat(250)}`)).q).toHaveLength(200);
  });

  it("writes it back so a search can be shared and gone back to", () => {
    expect(searchHref("rent paid")).toBe("/search?q=rent+paid");
    expect(searchHref("")).toBe("/search");
  });
});

describe("a pasted path, in any common form", () => {
  it("splits on ›, >, / and \\, any case, extra spaces", () => {
    const parts = ["Land & Legal", "Land records", "7-12 Extract.pdf"];
    expect(pathParts("Land & Legal › Land records › 7-12 Extract.pdf")).toEqual(parts);
    expect(pathParts("Land & Legal>Land records >  7-12 Extract.pdf")).toEqual(parts);
    expect(pathParts(" Land & Legal/Land records/7-12 Extract.pdf ")).toEqual(parts);
    expect(pathParts("Land & Legal\\Land   records\\7-12 Extract.pdf")).toEqual(parts);
  });

  it("reads a Windows, network, Mac or file:/// path from the computer", () => {
    expect(pathParts('"C:\\Users\\Tushar\\Documents\\Land & Legal\\7-12 Extract.pdf"')).toEqual([
      "Users",
      "Tushar",
      "Documents",
      "Land & Legal",
      "7-12 Extract.pdf",
    ]);
    expect(pathParts("D:\\Office\\Sale deeds")).toEqual(["Office", "Sale deeds"]);
    expect(pathParts("\\\\server\\share\\Land & Legal\\Sale deeds")).toEqual(["Land & Legal", "Sale deeds"]);
    expect(pathParts("/Users/tushar/Documents/Land & Legal/Sale deeds")).toEqual([
      "Users",
      "tushar",
      "Documents",
      "Land & Legal",
      "Sale deeds",
    ]);
    expect(pathParts("file:///C:/Users/Tushar/Land%20%26%20Legal/7-12%20Extract.pdf")).toEqual([
      "Users",
      "Tushar",
      "Land & Legal",
      "7-12 Extract.pdf",
    ]);
  });

  it("finds the file by the longest tail Kaagaz has, leaving out the computer's own folders", () => {
    expect(ids('"C:\\Users\\Tushar\\Documents\\Land & Legal\\Land records\\Survey 44, Wakad\\7-12 Extract.pdf"')).toEqual(["x1"]);
    expect(ids("/Users/tushar/Desktop/Land & Legal/Sale deeds/7-12 Extract old.pdf")).toEqual(["x2"]);
    expect(ids("file:///Users/tushar/OneDrive/Land%20%26%20Legal/Land%20records/Survey%2044%2C%20Wakad/7-12%20Extract.pdf")).toEqual(["x1"]);
    expect(ids("\\\\office-nas\\files\\Land & Legal\\Sale deeds\\7-12 Extract old.pdf")).toEqual(["x2"]);
  });

  it("takes a partial path, any case, the file's extension optional", () => {
    expect(ids("survey 44 › 7-12")).toEqual(["x1"]);
    expect(ids("SURVEY 44, WAKAD > 7-12 extract")).toEqual(["x1"]);
    expect(ids("sale deeds/7-12 extract old")).toEqual(["x2"]);
  });

  it("never names a folder the person can't see, even when the pasted path does", () => {
    const found = findByPath(tree, pathParts("C:\\Work\\Finance\\Salaries\\Pay slip.pdf"), named);
    expect(found.files.map((each) => each.id)).toEqual(["x3"]);
    const html = renderToStaticMarkup(createElement(SearchResults, { found: { tree, ...found, more: 0 } }));
    expect(html).toContain("Salaries");
    expect(html).not.toContain("Finance");
  });

  it("finds the folder when only the file's name differs, for its closest files", () => {
    const found = findByPath(tree, pathParts("Land & Legal/Land records/Survey 44, Wakad/missing.pdf"), named);
    expect(found).toEqual({ files: [], folders: [], closestIn: { id: "f3", path: "Land & Legal › Land records › Survey 44, Wakad" } });
  });

  it("lists the files first, each with its place, then the folders", () => {
    const found = findByPath(tree, pathParts("survey 44"), named);
    expect(found.files.map((each) => each.id)).toEqual(["x4"]);
    expect(found.folders).toEqual([{ id: "f3", path: "Land & Legal › Land records › Survey 44, Wakad" }]);
    const html = renderToStaticMarkup(createElement(SearchResults, { found: { tree, ...found, more: 0 } }));
    expect(html.indexOf(">Files<")).toBeLessThan(html.indexOf(">Folders<"));
    expect(html).toContain('href="/documents/x4"');
    expect(html).toContain("Land &amp; Legal › Land records › Survey 44, Wakad");
    expect(html).toContain('href="/documents?folder=f3"');
  });
});

describe("a pasted file link", () => {
  it("reads a document page's address on any host, or relative", () => {
    expect(documentFromLink(`https://kaagaz.example.in/documents/${ID}`)).toBe(ID);
    expect(documentFromLink(`http://localhost:3000/documents/${ID.toUpperCase()}?page=2`)).toBe(ID);
    expect(documentFromLink(`/documents/${ID}`)).toBe(ID);
    expect(documentFromLink("/documents/not-an-id")).toBeNull();
    expect(documentFromLink("Land & Legal/Sale deeds")).toBeNull();
  });

  it("opens a file the person can open; one they can't (or none) is the neutral line", async () => {
    api.getDocumentSummary.mockResolvedValueOnce({ id: ID });
    expect(await runSearch(`https://kaagaz.example.in/documents/${ID}`)).toEqual({ open: `/documents/${ID}` });
    api.getDocumentSummary.mockRejectedValueOnce(new ApiError(404, "not_found", "Not found."));
    const hidden = await runSearch(`/documents/${ID}`);
    expect("open" in hidden).toBe(false);
    const html = renderToStaticMarkup(createElement(SearchResults, { found: hidden as Exclude<typeof hidden, { open: string }> }));
    expect(html).toContain(NO_MATCH_TEXT.replace("'", "&#x27;")); // as HTML writes the apostrophe
    expect(api.listDocuments).not.toHaveBeenCalled();
  });

  it("asks for the last part's name, then matches the path, and the folder's files when only it matched", async () => {
    api.listFolders.mockResolvedValue({ items: folders });
    api.listDocuments.mockResolvedValueOnce({ items: named, total: 4 }).mockResolvedValueOnce({ items: [map], total: 1 });
    const found = await runSearch("Land & Legal > Land records > Survey 44, Wakad > missing.pdf");
    expect(api.listDocuments.mock.calls).toEqual([[{ q: "missing", limit: 50 }], [{ folder_id: "f3", limit: 25 }]]);
    expect("open" in found ? null : found.files.map((each) => each.id)).toEqual(["x4"]);
  });
});

describe("Copy path and Copy link", () => {
  it("copies only the folders the person sees, root first, then the file's name", () => {
    expect(filePath(tree, extract)).toBe("Land & Legal › Land records › Survey 44, Wakad › 7-12 Extract.pdf");
    expect(filePath(tree, slip)).toBe("Salaries › Pay slip.pdf"); // never Finance, above it
    expect(filePath(tree, file("x9", "Given.pdf", null, { shared_with_you: true }))).toBe("Shared with you › Given.pdf");
    expect(filePath(tree, file("x9", "Loose.pdf", null))).toBe("Loose.pdf");
    const detail = { display_name: "a.pdf", shared_with_you: false, folder: { id: "f2", name: "Land records", path: [{ id: "d1", name: "Land & Legal" }, { id: "f2", name: "Land records" }] } };
    expect(detailPath(detail as Pick<DocumentDetail, "folder" | "display_name" | "shared_with_you">)).toBe("Land & Legal › Land records › a.pdf");
    expect(detailPath({ display_name: "a.pdf", shared_with_you: true, folder: null })).toBe("Shared with you › a.pdf");
  });

  it("a copied path is found again by Search", () => {
    expect(ids(filePath(tree, extract))).toEqual(["x1"]);
  });

  it("says how to use what was copied, and says so when the browser refuses", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    expect(await copyFor("copyPath", "Land & Legal › a.pdf", ID)).toBe(PATH_COPIED);
    expect(PATH_COPIED).toBe("Path copied. Paste it in Search to find this file.");
    vi.stubGlobal("window", { location: { origin: "https://kaagaz.example.in" } });
    expect(await copyFor("copyLink", "", ID)).toBe(LINK_COPIED);
    expect(writeText.mock.calls).toEqual([["Land & Legal › a.pdf"], [`https://kaagaz.example.in/documents/${ID}`]]);
    expect(fileLink(ID, "https://a.example")).toBe(`https://a.example/documents/${ID}`);
    writeText.mockRejectedValueOnce(new Error("NotAllowedError"));
    expect(await copyFor("copyPath", "x", ID)).toBe(COPY_FAILED);
  });

  it("is in a file's menu, in the list and on the document page, never in Trash; and a folder's", () => {
    const item = { id: "x1", status: "ready", deleted_at: null, can_delete: false } as const;
    const words = (entries: ReturnType<typeof menuEntries>) => entries.map((entry) => (entry.kind === "action" ? entry.words : "download"));
    expect(words(menuEntries(item, false, "list"))).toEqual(expect.arrayContaining(["Copy path", "Copy link"]));
    expect(words(menuEntries(item, false, "document"))).toEqual(expect.arrayContaining(["Copy path", "Copy link"]));
    expect(words(menuEntries({ ...item, deleted_at: "2026-11-20T10:00:00Z" }, false, "list"))).not.toContain("Copy path");
    const actions = read("components/documents/FolderActions.tsx");
    expect(actions).toContain("copyText(folderPathText(tree, current.folder.id))");
    expect(actions).toContain("setCopied(done ? FOLDER_PATH_COPIED : COPY_FAILED)");
  });
});

describe("the screen", () => {
  const screen = read("components/search/SearchScreen.tsx");

  it("is one box, Find a file by name or path; inside documents is the Documents screen's", () => {
    expect(screen).toContain('export const SEARCH_LABEL = "Find a file by name or path";');
    expect(screen).not.toContain("searchDocuments");
    expect(screen).not.toContain("RadioPill");
    expect(read("components/documents/DocumentsView.tsx")).toContain("InsideSearchResults");
  });

  it("is a search landmark that keeps its words in the address", () => {
    expect(screen).toContain('<Form action="/search" role="search"');
    expect(screen).toContain('name="q"');
    expect(read("app/(app)/search/page.tsx")).toContain("<Suspense");
    expect(read("app/(app)/search/layout.tsx")).toContain('title: "Search"');
  });
});
