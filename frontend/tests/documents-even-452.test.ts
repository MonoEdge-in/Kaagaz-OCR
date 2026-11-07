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

describe("the documents list's rows (issue 452)", () => {
  const html = table([doc()]);

  it("gives the name one line, its whole words in the title, and one facts line under it", () => {
    const name = cell(html, "nameCell");
    expect(name).toContain('title="7-12 Utara Survey No 45 Wakad with mutation entries 2007 to 2026.pdf"');
    expect(rule(DOCS, ".name > span")).toMatch(/-webkit-line-clamp: 1;/);
    // Review panel, 29 Oct 2026: the owner comes early (its column is hidden up to 64rem), then the
    // type line of the narrowest table, inside the facts line, not a line of its own; the whole
    // line in its title.
    const line = "Confidential · Owner: Vrushit · PDF · Land record · 882.1 KB · v1 · Dated 15 Jun 2007 · In Land &amp; Legal";
    expect(name).toMatch(
      new RegExp(
        `<span class="[^"]*facts[^"]*" title="${line}"><span class="[^"]*factLabel[^"]*">(?:(?!</span></span>).)*</span></span><span class="[^"]*factOwner[^"]*">Owner: Vrushit</span><span class="[^"]*bandType[^"]*">PDF · Land record · 882.1 KB</span>`,
      ),
    );
    const table36 = DOCS.lastIndexOf("@container docs-list (min-width: 36rem) {");
    // Review panel, 29 Oct 2026: facts that did not fit wrapped to a line never shown, gone without
    // a trace at 36 to 64rem. The line is one line of text, cut at its end with "…".
    const facts = rule(DOCS, ".facts", table36);
    expect(facts).toMatch(/display: block;/);
    expect(facts).toMatch(/height: var\(--row-sub\);/);
    expect(facts).toMatch(/overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;/);
    expect(rule(DOCS, ".facts > *", table36)).not.toMatch(/flex|overflow|display/);
    expect(rule(DOCS, ".inFolder", table36)).toMatch(/display: inline;/);
    expect(DOCS).toMatch(/@container docs-list \(min-width: 36rem\) and \(max-width: 47\.98rem\) \{[^@]*\.bandType \{\s*display: inline;/);
  });

  it("puts one short count and Review on the status's second line, Review at its end", () => {
    const status = cell(html, "statusCell");
    expect(status).toMatch(/title="6 words to check and 2 pages to check">8 to check<\/span>/);
    expect(status).toContain(">Review<span class=\"visually-hidden\"> 6 words to check and 2 pages to check in ");
  });

  it("gives the Type cell \"PDF · Land record\" and the size, the same two lines on every row", () => {
    for (const row of [doc(), doc({ doc_type: null })]) {
      const type = cell(table([row]), "typeCell");
      expect(type.match(/<span class="[^"]*(typeLine|bandSize)[^"]*"/g)).toHaveLength(2);
      expect(type).toMatch(/bandSize[^"]*tabular">882.1 KB<\/span>/);
    }
    expect(cell(html, "typeCell")).toMatch(/title="PDF · Land record">PDF<span class="[^"]*docType[^"]*">Land record<\/span>/);
  });

  it("keeps the owner on one line, its whole name in the title", () => {
    expect(cell(html, "ownerCell")).toMatch(/<span class="[^"]*oneLine[^"]*" title="Vrushit">Vrushit<\/span>/);
    // Review panel, 29 Oct 2026: one rule for every line cut with "…".
    expect(DOCS).toMatch(/\.oneLine,\s*\.typeLine,\s*\.bandBy,\s*\.byName \{\s*overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;\s*\}/);
    expect(DOCS).toMatch(/\.oneLine,\s*\.typeLine \{\s*display: block;\s*\}/);
    // The tags under the name hide once the Label column shows them (folded into one rule).
    expect(DOCS).toMatch(/\.factLabel,\s*table\.docs \.factTags \{\s*display: none;\s*\}/);
  });

  it("puts Last updated's time on the first line and who on the second", () => {
    const updated = cell(html, "uploadedCell");
    expect(updated.indexOf("<time")).toBeLessThan(updated.indexOf("by Vrushit"));
  });
});
