// PRD 7 PR 11b (#604): Shared with me (?shared=1, the API's shared=true), a place like All
// documents for everyone, with no count; "Shared with you" in place of the path of a file in a
// folder the person can't see, from the API's own flag (lead decision 3), never a guess; and the
// one not-found text for a folder or document deleted, never there, or hidden.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { FolderNav } from "@/components/documents/FolderNav";
import { DocumentsTable } from "@/components/documents/DocumentsTable";
import { buildTree } from "@/components/documents/folderTree";
import { searchModeFor } from "@/components/documents/insideSearch";
import { ALL_DOCUMENTS, libraryFromSearch, libraryHref, listParams } from "@/components/documents/libraryQuery";
import type { LibraryState } from "@/components/documents/libraryQuery";
import { DetailsPanel } from "@/components/files/DetailsPanel";
import { DocumentsGrid } from "@/components/files/DocumentsGrid";
import { loadFailure } from "@/components/viewer/reprocess";
import { ApiError } from "@/lib/api";
import type { DocumentDetail, DocumentSummary, Folder } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ refresh: () => undefined }) }));

const FIN = "11111111-1111-4111-8111-111111111111";
const FOLDERS: Folder[] = [{ id: FIN, name: "Finance", parent_id: null, created_at: "2026-11-01T00:00:00Z", kind: "department" }];
const TREE = buildTree(FOLDERS);
const SHARED_VIEW: LibraryState = { ...ALL_DOCUMENTS, shared: true };
const nothing = () => undefined;

const doc = (over: Partial<DocumentSummary> = {}) =>
  ({
    id: "d1",
    display_name: "Sale deed.pdf",
    detected_type: "pdf",
    size_bytes: 1000,
    status: "ready",
    failure_code: null,
    status_message: "Ready.",
    page_count: 2,
    created_at: "2026-11-10T10:00:00Z",
    uploaded_by_name: "Priya",
    folder_id: FIN,
    tags: [],
    deleted_at: null,
    version_no: 1,
    family_id: "d1",
    can_delete: false,
    attempts: 1,
    max_attempts: 5,
    newer_version: null,
    progress: null,
    time_left: null,
    doc_type: null,
    languages: [],
    pages_unreadable: 0,
    pages_uncertain: 0,
    values_to_review: 0,
    ...over,
  }) as DocumentSummary;
// Given to me, in a folder I can't see: the server sends no folder and the flag.
const GIVEN = doc({ folder_id: null, shared_with_you: true });

const search = (text: string) => new URLSearchParams(text);

describe("Shared with me in the page address and the request", () => {
  it("survives a round trip, and sends shared=true", () => {
    const href = libraryHref(SHARED_VIEW);
    expect(href).toBe("/documents?shared=1");
    expect(libraryFromSearch(search("shared=1")).shared).toBe(true);
    expect(listParams(SHARED_VIEW, "", 0).shared).toBe(true);
    expect(listParams(ALL_DOCUMENTS, "", 0).shared).toBeUndefined();
  });

  it("is left by opening a folder or Trash, so it never narrows them unseen", () => {
    expect(libraryHref({ ...SHARED_VIEW, folderId: FIN })).not.toContain("shared");
    expect(libraryHref({ ...SHARED_VIEW, trash: true })).not.toContain("shared");
    expect(libraryFromSearch(search(`shared=1&folder=${FIN}`)).shared).toBe(false);
    expect(libraryFromSearch(search("shared=1&trash=1")).shared).toBe(false);
  });

  it("searches by name, as Trash does", () => {
    expect(searchModeFor("inside", SHARED_VIEW)).toBe("names");
    expect(searchModeFor("inside", ALL_DOCUMENTS)).toBe("inside");
  });
});

describe("the tree's Shared with me link", () => {
  const nav = (library: LibraryState) =>
    renderToStaticMarkup(createElement(FolderNav, { tree: TREE, library, loading: false, error: null, onRetry: nothing, onNavigate: nothing }));
  const link = (html: string, words: string) => html.slice(html.lastIndexOf("<a ", html.indexOf(`${words}</a>`)), html.indexOf(`${words}</a>`));

  it("comes after All documents for everyone, with the one-person icon and no count", () => {
    const html = nav(ALL_DOCUMENTS);
    expect(html.indexOf("Shared with me")).toBeGreaterThan(html.indexOf("All documents"));
    expect(html.indexOf("Shared with me")).toBeLessThan(html.indexOf("Finance"));
    const shared = link(html, "Shared with me");
    expect(shared).toContain('href="/documents?shared=1"');
    expect(shared).toContain('<circle cx="12" cy="8" r="4">'); // the kit's `user`, not `people`
    expect(shared).not.toMatch(/\d+<\/span>/);
  });

  it("is the current page there, and All documents is not; All documents leaves it", () => {
    const html = nav(SHARED_VIEW);
    expect(link(html, "Shared with me")).toContain('aria-current="page"');
    expect(link(html, "All documents")).not.toContain("aria-current");
    expect(link(html, "All documents")).toContain('href="/documents"');
  });
});

describe("Shared with you, in place of a hidden folder's path", () => {
  const table = (items: DocumentSummary[]) =>
    renderToStaticMarkup(
      createElement(DocumentsTable, {
        items,
        inTrash: false,
        me: { user: { id: "u1", role: "member", can_delete_forever: false, totp_enabled: false } } as never,
        now: Date.parse("2026-11-15T10:00:00Z"),
        tree: TREE,
        tags: [],
        suggestions: [],
        movedLeaves: false,
        onRemoved: nothing,
        onSaved: nothing,
        onFocusList: nothing,
        onTagCreated: nothing,
        onFolderCreated: nothing,
        onUploadVersion: nothing,
      }),
    );

  it("says Shared with you on the row from the API's flag, and never a folder", () => {
    const html = table([GIVEN]);
    expect(html).toContain("Shared with you");
    expect(html).not.toContain("In Finance");
  });

  it("says where a file the person can see the folder of is, as before", () => {
    const html = table([doc()]);
    expect(html).toContain("In Finance");
    expect(html).not.toContain("Shared with you");
    // Not a guess from the tree: a folder missing from it (still loading) says nothing.
    const loading = table([doc({ folder_id: "22222222-2222-4222-8222-222222222222" })]);
    expect(loading).not.toContain("Shared with you");
  });

  it("says it on the Grid card", () => {
    const grid = renderToStaticMarkup(createElement(DocumentsGrid, { items: [GIVEN, doc({ id: "d2" })], label: "Your documents" }));
    expect(grid.match(/Shared with you/g)).toHaveLength(1);
  });

  it("says it in Details in place of Not in a department", () => {
    const detail = (over: Partial<DocumentDetail>) =>
      renderToStaticMarkup(
        createElement(DetailsPanel, { detail: { ...GIVEN, folder: null, ...over } as DocumentDetail, onSaved: nothing, people: [] }),
      );
    expect(detail({})).toContain("<dd>Shared with you</dd>");
    expect(detail({ shared_with_you: false })).not.toContain("Shared with you");
  });
});

describe("the not-found texts", () => {
  it("are one text for a document deleted, never there, or hidden, with no Try again", () => {
    expect(loadFailure(new ApiError(404, "not_found", "x"))).toEqual({
      message: "We couldn't find this document. It may have been moved or deleted, or you may not have access to it.",
      retry: false,
    });
    expect(loadFailure(new ApiError(503, "busy", "x")).retry).toBe(true);
  });
});
