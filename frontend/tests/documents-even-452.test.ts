// Issue 452 (owner, 29 Oct 2026): "Not symmetric UI: the text goes up and down". The documents
// grid and list keep one fixed shape per card and per row:
// - Grid: the name in two lines' room (cut with "…", whole in its title), one line of chips,
//   and "Owner · date" pinned to the foot, never wrapping inside the date. The whole card is the
//   link and its hover the cue; no underline on the name, the focus ring kept.
// - List: every cell a first line and at most one line under it: the name, then one facts line;
//   the chip, then "9 to check" and Review at the line's end; "PDF · Land record", then the size.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { DocumentsTable } from "@/components/documents/DocumentsTable";
import { buildTree } from "@/components/documents/folderTree";
import { DocumentsGrid } from "@/components/files/DocumentsGrid";
import type { DocumentSummary, Folder } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ refresh: () => undefined }) }));

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const FILES = read("components/files/Files.module.css");
const DOCS = read("components/documents/Documents.module.css");
const GLOBALS = read("app/globals.css");

const LAND = "11111111-1111-4111-8111-111111111111";
const FOLDERS: Folder[] = [
  { id: LAND, name: "Land & Legal", parent_id: null, created_at: "2026-10-01T10:00:00Z", kind: "department", sort_order: 0 },
];

const doc = (over: Partial<DocumentSummary> = {}): DocumentSummary =>
  ({
    id: "d1",
    display_name: "7-12 Utara Survey No 45 Wakad with mutation entries 2007 to 2026.pdf",
    detected_type: "pdf",
    size_bytes: 903_270,
    status: "ready",
    failure_code: null,
    status_message: "Ready.",
    page_count: 2,
    created_at: "2026-10-15T10:00:00Z",
    uploaded_by_name: "Vrushit",
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
    doc_type: "land_record",
    languages: ["mr"],
    pages_unreadable: 2,
    pages_uncertain: 0,
    values_to_review: 6,
    owner: { id: "u-v", name: "Vrushit" },
    sensitivity: "confidential",
    document_date: "2007-06-15",
    updated_at: "2026-10-28T10:00:00Z",
    updated_by: "Vrushit",
    department: { id: LAND, name: "Land & Legal" },
    pending_suggestion: null,
    ...over,
  }) as DocumentSummary;

function table(items: DocumentSummary[]): string {
  const me = { user: { role: "member", can_delete_forever: false, totp_enabled: false } } as never;
  const noop = () => undefined;
  return renderToStaticMarkup(
    createElement(DocumentsTable, {
      items,
      inTrash: false,
      me,
      now: Date.parse("2026-10-29T06:00:00Z"),
      tree: buildTree(FOLDERS),
      tags: [],
      suggestions: [],
      movedLeaves: false,
      onRemoved: noop,
      onSaved: noop,
      onFocusList: noop,
      onTagCreated: noop,
      onFolderCreated: noop,
      onUploadVersion: noop,
    }),
  );
}

/** The body of the first rule whose selector is exactly `selector`, after `from` in the text. */
function rule(css: string, selector: string, from = 0): string {
  const at = css.indexOf(`${selector} {`, from);
  expect(at, selector).toBeGreaterThanOrEqual(0);
  return css.slice(at, css.indexOf("}", at));
}

/** A cell's markup: from its <td class="... name ..."> to its </td>. */
function cell(html: string, name: string): string {
  const start = html.search(new RegExp(`<td role="cell" class="[^"]*${name}`));
  expect(start, name).toBeGreaterThanOrEqual(0);
  return html.slice(start, html.indexOf("</td>", start));
}

describe("the documents grid's cards (issue 452)", () => {
  it("clamps the name to two lines and keeps two lines' room for a short one", () => {
    const name = rule(FILES, ".cardName");
    expect(name).toMatch(/-webkit-line-clamp: 2;/);
    expect(name).toMatch(/line-clamp: 2;/);
    expect(name).toMatch(/overflow: hidden;/);
    expect(name).toMatch(/min-height: 2lh;/);
  });

  it("keeps the chips on one line, as tall with a label chip as without", () => {
    const facts = rule(FILES, ".cardFacts");
    expect(facts).toMatch(/white-space: nowrap;/);
    expect(facts).toMatch(/min-height: var\(--badge-h\);/);
    expect(facts).not.toMatch(/flex-wrap: wrap/);
  });

  it("pins the owner and date to the foot, and never breaks inside the date", () => {
    const meta = rule(FILES, ".cardMeta");
    expect(meta).toMatch(/margin-top: auto;/);
    expect(meta).toMatch(/white-space: nowrap;/);
    expect(rule(FILES, ".cardDate")).toMatch(/flex: none;/);
    expect(rule(FILES, ".cardOwner")).toMatch(/text-overflow: ellipsis;/);
  });

  it("makes the whole card the link, with no underline on hover and the focus ring kept", () => {
    expect(rule(FILES, ".card")).toMatch(/position: relative;/);
    expect(rule(FILES, ".cardName::after")).toMatch(/position: absolute;\s*inset: 0;/);
    expect(FILES).toMatch(/@media \(hover: hover\) and \(pointer: fine\) \{[^@]*\.card \.cardName:hover \{\s*text-decoration: none;/);
    expect(FILES).not.toMatch(/\.cardName[^{]*\{[^}]*outline: none/);
    expect(GLOBALS).toMatch(/:focus-visible \{\s*outline: 2px solid var\(--focus\);/);
  });

  it("keeps the whole name in a title and says Owner and Last updated to a screen reader", () => {
    const html = renderToStaticMarkup(createElement(DocumentsGrid, { items: [doc()], label: "Documents" }));
    expect(html).toContain('title="7-12 Utara Survey No 45 Wakad with mutation entries 2007 to 2026.pdf"');
    expect(html).toMatch(/<span class="[^"]*cardOwner[^"]*" title="Vrushit"><span class="visually-hidden">Owner: <\/span>Vrushit<\/span><span aria-hidden="true">·<\/span>/);
    expect(html).toMatch(/<span class="[^"]*cardDate[^"]*"><span class="visually-hidden">Last updated <\/span>28 Oct 2026<\/span>/);
  });

  // Review panel, 29 Oct 2026: the card's link (its ::after) lay over the label chip, so the chip's
  // description never showed; and a long owner or type was cut with "…" and had no title at all.
  it("lifts the label chip, the owner and the type over the card's link, the cut ones with their whole words in a title", () => {
    expect(FILES).toMatch(/\.cardOwner,\s*\.cardFacts :global\(\.label-chip\),\s*\.cardFacts > :last-child \{\s*position: relative;\s*z-index: 1;\s*\}/);
    const owner = "Vrushit Patel of the Land and Legal department";
    const html = renderToStaticMarkup(createElement(DocumentsGrid, { items: [doc({ owner: { id: "u-v", name: owner } })], label: "Documents" }));
    expect(html).toContain(`title="${owner}"`);
    expect(html).toMatch(/<span title="Land record">Land record<\/span><\/span>/); // the facts line's last child
    expect(html).toContain('title="A label for sensitive papers.'); // the chip's own description (LabelChip)
  });
});

// Changed on purpose (handoff sections 20 and 21, Documents.html, 5 Nov 2026): the compact list.
// One 44px line per file in the table, each cell cut with "…" and its whole words in a title; the
// facts line under the name stays for the cards below 36rem only.
describe("the documents list's rows (issue 452)", () => {
  const html = table([doc()]);
  const table36 = DOCS.indexOf("@container docs-list (min-width: 36rem) {");

  it("gives the name one line, its whole words in the title, the version beside it", () => {
    const name = cell(html, "nameCell");
    expect(name).toContain('title="7-12 Utara Survey No 45 Wakad with mutation entries 2007 to 2026.pdf"');
    expect(name).not.toContain("k-file-ic"); // no file icon: the extension is in the name
    expect(name).toMatch(/<span class="k-ver [^"]*verChip[^"]*">v1<\/span>/);
    expect(rule(DOCS, ".name > span")).toMatch(/overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;/);
    // The facts line is the cards' alone; the table has a column for each fact.
    const line = "Confidential · Owner: Vrushit · PDF · Land record · 882.1 KB · v1 · Dated 15 Jun 2007 · In Land &amp; Legal";
    expect(name).toContain(`title="${line}"`);
    expect(DOCS.slice(table36)).toMatch(/\.facts,\s*\.factTags,\s*\.newerVersion,\s*table\.docs \.nameCell > ul \{\s*display: none;/);
  });

  it("links the count to check, one short pill, its words in full in its title and read out", () => {
    const status = cell(html, "statusCell");
    expect(status).toMatch(/class="k-check-link" title="6 words to check and 2 pages to check" href="\/documents\/d1">8 to check/);
    expect(status).toContain('<span class="visually-hidden"> in 7-12 Utara');
    // A clean read says nothing to see, only to a screen reader.
    const clean = cell(table([doc({ words_unsure: 0, pages_unreadable: 0, pages_uncertain: 0, values_to_review: 0 })]), "statusCell");
    expect(clean).not.toContain("k-check-link");
  });

  it("gives the Type cell \"PDF · Land record\" on one line, the size in its title", () => {
    for (const row of [doc(), doc({ doc_type: null })]) {
      const type = cell(table([row]), "typeCell");
      expect(type.match(/<span class="[^"]*typeLine[^"]*"/g)).toHaveLength(1);
      expect(type).toContain("882.1 KB\"");
    }
    expect(cell(html, "typeCell")).toMatch(/title="PDF · Land record · 882.1 KB">PDF<span class="[^"]*docType[^"]*">Land record<\/span>/);
  });

  it("keeps the owner on one line, its whole name in the title", () => {
    expect(cell(html, "ownerCell")).toMatch(/<span class="[^"]*oneLine[^"]*" title="Vrushit">Vrushit<\/span>/);
    // One rule for every line cut with "…", and every table cell is one line too.
    expect(DOCS).toMatch(/\.oneLine,\s*\.typeLine,\s*\.updatedLine \{\s*display: block;\s*overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;\s*\}/);
    expect(rule(DOCS, "table.docs td", table36)).toMatch(/overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;/);
  });

  it("puts Updated and who changed it on one line: \"2h ago · Vrushit\"", () => {
    const updated = cell(html, "uploadedCell");
    expect(updated).toMatch(/class="[^"]*updatedLine[^"]*"/);
    expect(updated.indexOf("<time")).toBeLessThan(updated.lastIndexOf(">Vrushit<"));
    expect(updated).toContain('<span aria-hidden="true"> · </span>');
    expect(html).not.toMatch(/byCell|byColumn|versionCell|versionColumn/);
  });
});
