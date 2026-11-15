import { describe, expect, it } from "vitest";
import { kpiTiles } from "@/components/admin/logic";
import { downloadHref, menuEntries } from "@/components/documents/library";
import { canReprocess } from "@/components/viewer/reprocess";
import { statusChip } from "@/lib/format";
import type { DocumentStatus } from "@/lib/types";

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
    for (const status of ["ready", "failed", "needs_reader", "locked"] as DocumentStatus[]) {
      expect(canReprocess("admin", { status, deleted_at: null, locked: true }), status).toBe(false);
    }
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
