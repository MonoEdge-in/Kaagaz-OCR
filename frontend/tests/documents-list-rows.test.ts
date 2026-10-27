// Issue 371 (27 Oct 2026), the documents list at 1440 px:
// - "Unknown" in every Type cell: no data or mapping bug. doc_type is null until the AI
//   model's card has said what the document is (backend pipeline: no card with the model
//   off or down, and none yet while it is read after Ready), so the kind line is left out
//   then, and shown once there is one.
// - Tall rows and names cut short: Status shared the free width half and half with Name;
//   it has its own 11rem now, and "In <folder>" sits on the facts' line, not a line of its own.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { DocumentsTable } from "@/components/documents/DocumentsTable";
import { buildTree } from "@/components/documents/folderTree";
import type { DocumentSummary, Folder } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ refresh: () => undefined }) }));

const LAND = "11111111-1111-4111-8111-111111111111";
const FOLDERS: Folder[] = [
  { id: LAND, name: "Land & Legal", parent_id: null, created_at: "2026-10-01T10:00:00Z", kind: "department", sort_order: 0 },
];

const doc = (over: Partial<DocumentSummary> = {}): DocumentSummary =>
  ({
    id: "d1",
    display_name: "7-12 Survey 44 Wakad extract with mutation entries.pdf",
    detected_type: "pdf",
    size_bytes: 1000,
    status: "ready",
    failure_code: null,
    status_message: "Ready.",
    page_count: 2,
    created_at: "2026-10-15T10:00:00Z",
    uploaded_by_name: "Priya",
    folder_id: LAND,
    tags: [],
    deleted_at: null,
    version_no: 1,
    family_id: "d1",
    can_delete: true,
    attempts: 1,
    max_attempts: 5,
    newer_version: null,
    progress: null,
    time_left: null,
    doc_type: null,
    languages: ["mr"],
    pages_unreadable: 0,
    pages_uncertain: 0,
    values_to_review: 0,
    owner: { id: "u-priya", name: "Priya" },
    sensitivity: "confidential",
    document_date: "2026-06-12",
    updated_at: "2026-10-16T10:00:00Z",
    updated_by: "Anita",
    department: { id: LAND, name: "Land & Legal" },
    pending_suggestion: null,
    ...over,
  }) as DocumentSummary;

function table(items: DocumentSummary[]): string {
  const me = { user: { role: "member", can_delete_forever: false, totp_enabled: false } } as never;
  return renderToStaticMarkup(
    createElement(DocumentsTable, {
      items,
      inTrash: false,
      me,
      now: Date.parse("2026-10-17T10:00:00Z"),
      tree: buildTree(FOLDERS),
      tags: [],
      suggestions: [],
      movedLeaves: false,
      sorting: { sort: "name", order: "asc", onSort: () => undefined },
      onRemoved: () => undefined,
      onSaved: () => undefined,
      onFocusList: () => undefined,
      onTagCreated: () => undefined,
      onFolderCreated: () => undefined,
      onUploadVersion: () => undefined,
    }),
  );
}

const css = readFileSync(join(__dirname, "..", "components/documents/Documents.module.css"), "utf8");

describe("the documents list's Type cell", () => {
  it("says no Unknown while the card has not said what the document is", () => {
    const html = table([doc()]);
    expect(html).not.toContain("Unknown");
    expect(html).toContain("PDF");
  });

  it("names the kind once the card has", () => {
    expect(table([doc({ doc_type: "land_record" })])).toContain(">Land record</span>");
  });
});

describe("a row of the documents list", () => {
  it("puts In <folder> on the facts' line under the name, not a line of its own", () => {
    const html = table([doc()]);
    // The folder is the facts span's last child: its own span closes, then the facts span.
    expect(html).toMatch(/<span class="[^"]*inFolder[^"]*">In Land &amp; Legal<\/span><\/span>/);
    expect(html).toMatch(/<span class="[^"]*facts[^"]*">(?:(?!<\/td>).)*In Land &amp; Legal/);
  });

  it("gives Status its own width, so Name takes the free width", () => {
    const tableBlock = css.slice(css.indexOf("@container docs-list (min-width: 36rem) {\n  table.docs {\n    table-layout: fixed;"));
    expect(tableBlock).toMatch(/\.statusColumn \{\s*width: 11rem;\s*\}/);
    expect(tableBlock).not.toMatch(/\.nameColumn \{[^}]*width:/);
  });

  it("keeps the compact rhythm: a 48px row with lines of --row-sub under the first", () => {
    expect(css).toMatch(/table\.docs td \{\s*height: 48px;\s*padding-block: var\(--space-2\);/);
    expect(css).toMatch(/\.inFolder,\s*\.bandSize,\s*\.bandBy \{\s*line-height: var\(--row-sub\);/);
  });
});
