// The Search screen (components/search/SearchScreen.tsx; docs/architecture.md, The screens): the top
// bar's search lands on /search, inside documents first, names on request.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { NAMES_LIMIT, namesCountText, searchFromAddress, searchHref } from "@/components/search/SearchScreen";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("the search's address", () => {
  it("reads the question and where to search, inside documents by default", () => {
    expect(searchFromAddress(new URLSearchParams("q=rent"))).toEqual({ q: "rent", mode: "inside" });
    expect(searchFromAddress(new URLSearchParams("q=+Mehta+&in=names"))).toEqual({ q: "Mehta", mode: "names" });
    expect(searchFromAddress(new URLSearchParams("in=elsewhere"))).toEqual({ q: "", mode: "inside" });
  });

  it("keeps a question to 200 characters, as the server takes it", () => {
    expect(searchFromAddress(new URLSearchParams(`q=${"a".repeat(250)}`)).q).toHaveLength(200);
  });

  it("writes it back so a search can be shared and gone back to", () => {
    expect(searchHref("rent paid", "inside")).toBe("/search?q=rent+paid");
    expect(searchHref("Mehta", "names")).toBe("/search?q=Mehta&in=names");
    expect(searchHref("", "inside")).toBe("/search");
    expect(searchHref("सातबारा", "inside")).toBe(`/search?q=${encodeURIComponent("सातबारा")}`);
  });
});

describe("the Names results", () => {
  it("say how many, and that there are more", () => {
    expect(namesCountText(1, 1)).toBe("1 document is named like this.");
    expect(namesCountText(7, 7)).toBe("7 documents are named like this.");
    expect(namesCountText(NAMES_LIMIT, 40)).toBe("The first 25 of 40 documents named like this.");
  });
});

describe("the screen", () => {
  const screen = read("components/search/SearchScreen.tsx");

  it("searches inside documents with the documents screen's own results, words marked", () => {
    expect(screen).toContain("searchDocuments({ q })");
    expect(screen).toContain("<InsideSearchResults");
    expect(screen).toContain("listDocuments({ q, limit: NAMES_LIMIT })");
  });

  it("is a search landmark that keeps its question in the address", () => {
    expect(screen).toContain('<Form action="/search" role="search"');
    expect(screen).toContain('name="q"');
    expect(read("app/(app)/search/page.tsx")).toContain("<Suspense");
    expect(read("app/(app)/search/layout.tsx")).toContain('title: "Search"');
  });
});
