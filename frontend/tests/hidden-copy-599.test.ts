// PRD 7 PR 7 (#599): a copy of a file the uploader can't see says only that it is in Kaagaz (no
// name, no place, no link), and a restored folder whose place holds a folder the person can't see
// says no place.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DuplicateItem } from "@/components/documents/FolderUploadRow";
import { rowKind } from "@/components/documents/batchTray";
import { buildTree } from "@/components/documents/folderTree";
import { HIDDEN_COPY, uploadStateText } from "@/components/documents/logic";
import { folderBackMessage, folderRestoredMessage } from "@/components/documents/trash";
import type { Folder, FolderUploadItem } from "@/lib/types";

const folder = { id: "f1", name: "Notes", parent_id: null, created_at: "2026-01-01T00:00:00Z" } as Folder;

describe("a copy of a file the uploader can't see", () => {
  it("says the PRD's sentence and counts as already in", () => {
    expect(HIDDEN_COPY).toBe("This file is already in Kaagaz, in a place you can't see. Ask your admin if you need it.");
    expect(uploadStateText({ kind: "elsewhere" })).toBe("Already in Kaagaz");
    expect(rowKind({ state: { kind: "elsewhere" } })).toBe("already");
  });
});

describe("a restored folder with a hidden place", () => {
  it("names no place", () => {
    const restored = { folder, location: null, renamed: false, relocated: false };
    expect(folderRestoredMessage(restored)).toBe("Folder restored.");
    expect(folderBackMessage(restored)).toBe("Its folder “Notes” is back.");
  });
});

describe("a folder upload's copy of a file the person can't see", () => {
  it("shows the file's own name and the sentence, and no link or place", () => {
    const item: FolderUploadItem = {
      id: "i1",
      seq: 0,
      path_key: "k",
      name: "bill.pdf",
      folder_id: "f1",
      size: 10,
      state: "duplicate",
      failure_code: null,
      document_id: null,
      document: null,
      hidden: true,
    };
    const html = renderToStaticMarkup(createElement(DuplicateItem, { item, tree: buildTree([folder]) }));
    expect(html).toContain("bill.pdf");
    expect(html).toContain("in a place you can&#x27;t see");
    expect(html).not.toContain("<a ");
    expect(html).not.toContain("Notes");
  });
});
