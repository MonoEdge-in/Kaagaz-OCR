import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { kpiTiles } from "@/components/admin/logic";
import { rowKind } from "@/components/documents/batchTray";
import { LockedCopy } from "@/components/documents/UploadArea";
import { LOCKED_COPY, copyState, uploadStateText } from "@/components/documents/logic";
import { downloadHref, menuEntries } from "@/components/documents/library";
import { canReprocess } from "@/components/viewer/reprocess";
import { statusChip } from "@/lib/format";
import type { DocumentStatus, UploadResult } from "@/lib/types";

// PRD 7 (#605): a locked file is never downloaded, read again or shown as failed, whatever
// status it is in (a locked version being prepared is received or clean).
describe("a locked file (PRD 7, #605)", () => {
  const statuses: DocumentStatus[] = ["clean", "ready", "needs_reader", "locked"];

  it("offers no download, in any status", () => {
    for (const status of statuses) {
      expect(downloadHref({ id: "d-1", status, locked: true }), status).toBeNull();
    }
    expect(downloadHref({ id: "d-1", status: "ready", locked: false })).toBe("/api/documents/d-1/file?download=1");
  });

  it("is never read again, even by an admin", () => {
    for (const status of ["ready", "needs_reader", "locked"] as DocumentStatus[]) {
      expect(canReprocess("admin", { status, deleted_at: null, locked: true }), status).toBe(false);
    }
  });

  // Changed on purpose (review, 15 Nov 2026): a locked version whose page pictures were not made
  // offers Retry, which only prepares it again (the worker never reads a locked version).
  it("offers Retry when its preparing failed", () => {
    expect(canReprocess("reviewer", { status: "failed", deleted_at: null, locked: true })).toBe(true);
    expect(canReprocess("member", { status: "failed", deleted_at: null, locked: true })).toBe(false);
  });

  it("lists no Download original in its menu", () => {
    const entries = menuEntries({ id: "d-1", status: "clean", deleted_at: null, can_delete: true, locked: true }, false);
    expect(entries.some((entry) => entry.kind === "download")).toBe(false);
  });

  it("says Locked, calmly, never Failed", () => {
    expect(statusChip("locked")).toMatchObject({ text: "Locked", tone: "neutral" });
  });

  it("counts among the documents on the Numbers tab", () => {
    const tiles = kpiTiles({
      documents_by_status: { ready: 2, locked: 3 },
      open_review_items: 0,
    } as unknown as Parameters<typeof kpiTiles>[0]);
    expect(tiles[0]).toMatchObject({ label: "Total documents", value: "5" });
  });
});

// PRD 7 section 5 (#605, p2:91): uploading a copy of a locked file the person may open answers
// {document: null, locked: {id}}; the row says the PRD's line with a link, and never reads a
// document that isn't there (it crashed on result.document.id).
describe("an upload of a copy of a locked file", () => {
  const reply: UploadResult = { document: null, duplicate: true, in_trash: false, locked: { id: "d-9" } };

  it("is a locked copy row, counted as already in", () => {
    if (reply.document !== null) throw new Error("the reply names no document");
    const state = copyState(reply);
    expect(state).toEqual({ kind: "locked", documentId: "d-9" });
    expect(uploadStateText(state)).toBe("Already in Kaagaz, locked");
    expect(rowKind({ state })).toBe("already");
    expect(copyState({ document: null, duplicate: true, in_trash: false, hidden: true })).toEqual({ kind: "elsewhere" });
  });

  it("says the PRD's line with a link to the file", () => {
    expect(LOCKED_COPY).toBe("A copy already exists and is locked.");
    const html = renderToStaticMarkup(createElement(LockedCopy, { documentId: "d-9", name: "deed.pdf" }));
    expect(html).toContain("A copy already exists and is locked.");
    expect(html).toContain('href="/documents/d-9"');
  });
});
