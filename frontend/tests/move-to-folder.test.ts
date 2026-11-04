// Move to folder (Tushar, 16 Oct 2026): "Already present files have no option to move into the
// folders I made" and "or suggest folder name also". A row's menu and the selection bar open one
// Move dialog (folders indented, the current one disabled, Suggested first); the New folder forms
// offer names from the kinds of documents listed.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { DocumentSummary, Folder, Me } from "@/lib/types";

vi.mock("@/lib/session", () => ({ useSession: () => ({ refresh: () => undefined }) }));

const { MoveForm } = await import("@/components/documents/DocumentActions");
const { DocumentsTable } = await import("@/components/documents/DocumentsTable");
const { NameChips } = await import("@/components/documents/FolderActions");
const { FolderPicker } = await import("@/components/documents/FolderPicker");
const { buildTree } = await import("@/components/documents/folderTree");
const { folderNameSuggestions, menuEntries, moveSuggestion, movableItems, movedMessage, typeFolderName } =
  await import("@/components/documents/library");

const folder = (id: string, name: string, parent_id: string | null = null): Folder => ({
  id,
  name,
  parent_id,
  created_at: "2026-10-16T10:00:00Z",
});
const FOLDERS = [folder("f-inv", "Invoices"), folder("f-sup", "Suppliers"), folder("f-26", "2026", "f-sup")];
const TREE = buildTree(FOLDERS);

const doc = (over: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id: "d1",
  display_name: "bill.pdf",
  detected_type: "pdf",
  size_bytes: 1000,
  status: "ready",
  failure_code: null,
  status_message: "Ready.",
  page_count: 1,
  created_at: "2026-10-15T10:00:00Z",
  uploaded_by_name: null,
  folder_id: null,
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
  doc_type: "invoice",
  languages: [],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
  ...over,
});

const words = (entries: ReturnType<typeof menuEntries>) =>
  entries.map((entry) => (entry.kind === "download" ? "Download original" : entry.words));

const moveForm = (items: DocumentSummary[], tree = TREE, suggestions: string[] = []) =>
  renderToStaticMarkup(
    createElement(MoveForm, {
      items,
      tree,
      suggestions,
      onSaved: () => undefined,
      onCancel: () => undefined,
    }),
  );

/** The radio for this place, and whether it is disabled and checked. */
function radio(html: string, words: string): { disabled: boolean; checked: boolean } {
  const items = html.split("<li").slice(1);
  const item = items.find((li) => li.includes(words));
  if (!item) throw new Error(`no choice "${words}" in ${html}`);
  const input = /<input[^>]*>/.exec(item)?.[0] ?? "";
  return { disabled: /\sdisabled=""/.test(input), checked: /\schecked=""/.test(input) };
}

describe("the row menu", () => {
  it("has Move to folder out of Trash, never in Trash", () => {
    expect(words(menuEntries(doc(), false))).toContain("Move to folder");
    expect(words(menuEntries(doc({ deleted_at: "2026-10-16T10:00:00Z", can_delete: true }), true))).not.toContain(
      "Move to folder",
    );
  });
});

describe("the Move dialog", () => {
  // Changed on purpose (#484, folders up to 100 levels): the places are a tree that opens one
  // folder at a time, as the Documents tree does, so a tree of thousands stays one short list.
  it("lists All documents, the top-level folders, a button to open one that holds more, and New folder…", () => {
    const html = moveForm([doc({ doc_type: "other" })]);
    const order = ["All documents (no folder)", "Invoices", "Suppliers", "New folder…"].map((w) => html.indexOf(w));
    expect(order.every((at) => at >= 0)).toBe(true);
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(html).toContain('aria-label="Show folders in Suppliers"');
    expect(html).not.toContain("2026");
    expect(html.match(/type="radio"/g)).toHaveLength(4);
  });

  it("opens the way to where the documents are now, the nested folder indented", () => {
    const html = moveForm([doc({ folder_id: "f-26", doc_type: "other" })]);
    expect(html).toMatch(/--depth:1[^]*?2026/);
    expect(html).toContain('aria-label="Hide folders in Suppliers"');
  });

  it("draws one level of a 5,050-folder tree, with Find a folder", () => {
    const big = buildTree(
      Array.from({ length: 50 }, (_, top) => [
        folder(`T${top}`, `Top ${top}`),
        ...Array.from({ length: 100 }, (_, child) => folder(`T${top}-${child}`, `Child ${child}`, `T${top}`)),
      ]).flat(),
    );
    const html = moveForm([doc({ doc_type: "other" })], big);
    expect(html.match(/type="radio"/g)).toHaveLength(52); // All documents, 50 folders, New folder…
    expect(html).toContain("Find a folder");
  });

  it("never lets a disabled place be chosen", () => {
    const html = renderToStaticMarkup(
      createElement(FolderPicker, {
        tree: TREE,
        name: "to",
        choice: null,
        onChoose: () => undefined,
        disabled: (node) => node.folder.id === "f-sup",
      }),
    );
    expect(radio(html, "Suppliers").disabled).toBe(true);
    expect(radio(html, "Invoices").disabled).toBe(false);
  });

  it("marks where the document is now Current and disables it; the others can be chosen", () => {
    const html = moveForm([doc({ folder_id: "f-26", doc_type: "other" })]);
    expect(radio(html, "2026")).toEqual({ disabled: true, checked: false });
    expect(html).toMatch(/2026<span class="visually-hidden">, Level 2<\/span><span[^>]*>Current<\/span>/);
    expect(radio(html, "Suppliers").disabled).toBe(false);
    expect(radio(html, "All documents").disabled).toBe(false);
    // Not in a folder: All documents is the current place.
    expect(radio(moveForm([doc({ doc_type: "other" })]), "All documents").disabled).toBe(true);
  });

  // Changed on purpose (Tushar, 16 Oct 2026): the suggested folder is tagged where it is in
  // the tree, not copied to the top, so the column of choices stays even.
  it("tags the folder named for the document's kind Suggested, in its place, and chooses it", () => {
    const html = moveForm([doc({ doc_type: "invoice" })]);
    const first = html.split("<li")[1] ?? "";
    expect(first).toContain("All documents (no folder)");
    expect(html).toMatch(/Invoices <span[^>]*>Suggested<\/span>/);
    expect(radio(html, "Suggested").checked).toBe(true);
    expect(html.match(/Invoices/g)).toHaveLength(1); // not listed twice
  });

  it("offers New folder: <name> first when no folder has that name", () => {
    const html = moveForm([doc({ doc_type: "bank_statement" })]);
    const first = html.split("<li")[1] ?? "";
    expect(first).toContain("New folder: Bank statements");
    expect(first).toContain("Suggested");
    expect(radio(html, "New folder: Bank statements").checked).toBe(true);
  });

  it("keeps a suggested folder's subfolders under it, with one choice for it", () => {
    const tree = buildTree([...FOLDERS, folder("f-sep", "September", "f-inv")]);
    const html = moveForm([doc()], tree);
    expect(html.match(/type="radio"[^>]*value="f-inv"/g)).toHaveLength(1); // one choice, first
    expect(html.indexOf("September")).toBeGreaterThan(html.lastIndexOf(">Invoices<"));
  });

  it("suggests nothing for documents of different kinds, or already in that folder", () => {
    expect(moveForm([doc(), doc({ id: "d2", doc_type: "receipt" })])).not.toContain("Suggested");
    expect(moveForm([doc({ folder_id: "f-inv" })])).not.toContain("Suggested");
  });
});

describe("moveSuggestion", () => {
  it("matches a folder name in any letter case, the one nearest the top", () => {
    const tree = buildTree([folder("deep", "Invoices", "f-sup"), folder("f-sup", "Suppliers"), folder("top", "INVOICES")]);
    expect(moveSuggestion([doc()], tree)).toEqual({ folderId: "top" });
    expect(moveSuggestion([doc({ doc_type: null })], tree)).toBeNull();
    expect(moveSuggestion([doc({ doc_type: "contract" })], tree)).toEqual({ newName: "Contracts" });
  });
});

describe("folder name suggestions", () => {
  it("names folders after the kinds of documents, plural, like auto-filing does", () => {
    expect(typeFolderName("invoice")).toBe("Invoices");
    expect(typeFolderName("bank_statement")).toBe("Bank statements");
    expect(typeFolderName("land_record")).toBe("Land records");
    expect(typeFolderName("other")).toBeNull();
    expect(typeFolderName(null)).toBeNull();
  });

  it("leaves out names a folder already has (any case), most common kind first, at most 4", () => {
    const docs = [
      ...["invoice", "receipt", "receipt", "bank_statement", "letter", "letter", "letter", "form", "register"],
      "other",
      null,
    ].map((doc_type) => ({ doc_type }) as Pick<DocumentSummary, "doc_type">);
    expect(folderNameSuggestions(docs, [{ name: "invoices " }])).toEqual([
      "Letters",
      "Receipts",
      "Bank statements",
      "Forms",
    ]);
    expect(folderNameSuggestions(docs, [], 2)).toEqual(["Letters", "Receipts"]);
    expect(folderNameSuggestions([], [])).toEqual([]);
  });

  it("shows them as one-press square chips in the New folder form", () => {
    const html = renderToStaticMarkup(
      createElement(NameChips, { names: ["Receipts", "Letters"], picked: "Letters ", onPick: () => undefined }),
    );
    expect(html).toContain("Suggested names");
    expect(html).toMatch(/aria-pressed="false"[^>]*>Receipts<\/button>/);
    expect(html).toMatch(/data-active="true" aria-pressed="true"[^>]*>Letters<\/button>/);
    expect(renderToStaticMarkup(createElement(NameChips, { names: [], picked: "", onPick: () => undefined }))).toBe("");
    // The "+" New folder form (FolderActions) and the Move dialog's New folder… both show them.
    const source = (file: string) => readFileSync(join(__dirname, "../components/documents", file), "utf8");
    expect(source("FolderActions.tsx")).toContain('{mode === "new" && <NameChips names={suggestions}');
    expect(source("DocumentActions.tsx")).toContain("<NameChips names={suggestions} picked={newName}");
    const css = readFileSync(join(__dirname, "../components/documents/Library.module.css"), "utf8");
    // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's chips are pills (desktop/Documents.html).
    expect(css).toMatch(/\.chip \{[^}]*border-radius: var\(--k-r-pill\);/);
  });
});

describe("Move to folder in the selection bar", () => {
  const me = { user: { role: "member", can_delete_forever: false, totp_enabled: false } } as unknown as Me;
  const table = (items: DocumentSummary[], inTrash = false) =>
    renderToStaticMarkup(
      createElement(DocumentsTable, {
        items,
        inTrash,
        me,
        now: Date.parse("2026-10-16T10:00:00Z"),
        tree: TREE,
        tags: [],
        suggestions: [],
        movedLeaves: false,
        onRemoved: () => undefined,
        onSaved: () => undefined,
        onFocusList: () => undefined,
        onTagCreated: () => undefined,
        onFolderCreated: () => undefined,
        onUploadVersion: () => undefined,
      }),
    );

  it("takes only ticked documents, never one in Trash", () => {
    const items = [doc(), doc({ id: "d2" }), doc({ id: "d3", deleted_at: "2026-10-16T10:00:00Z" })];
    expect(movableItems(items, new Set())).toEqual([]);
    expect(movableItems(items, new Set(["d2", "d3"])).map((item) => item.id)).toEqual(["d2"]);
  });

  it("is not in the bar until a row is ticked, and every row out of Trash can be ticked", () => {
    const html = table([doc(), doc({ id: "d2" })]); // can_delete false: a member's own view
    expect(html).not.toMatch(/Move to folder \(/);
    expect(html).toContain('aria-label="Select bill.pdf"');
    expect(table([doc({ deleted_at: "2026-10-16T10:00:00Z" })], true)).not.toContain("Select bill.pdf");
    const source = readFileSync(join(__dirname, "../components/documents/DocumentsTable.tsx"), "utf8");
    expect(source).toMatch(/\{movable\.length > 0 && \(\s*<button[^\n]*>\s*Move to folder \(\{movable\.length\}\)/);
  });

  // "Move files to a folder not working" (Tushar, 17:41): the move was made, but in All documents
  // the row stayed as it was and the news was only for screen readers. Now each row says its
  // folder, and every move (the dialog, the bar, a drop on a folder) shows in the toast.
  it("shows the folder each document is in on its row", () => {
    const html = table([doc({ folder_id: "f-26" }), doc({ id: "d2", display_name: "loose.pdf" })]);
    expect(html).toContain("In Suppliers / 2026");
    expect(html.match(/>In /g)).toHaveLength(1);
  });

  it("shows a move dropped on a folder in the toast, with Undo", () => {
    const view = readFileSync(join(__dirname, "../components/documents/DocumentsView.tsx"), "utf8");
    const drop = view.slice(view.indexOf("function dropDocument"), view.indexOf("function navigated"));
    expect(drop).toMatch(/afterSave\(\s*`Moved \$\{name\} to \$\{placeName\}\.`,\s*item\s*\?\s*async/);
    // Changed on purpose (issue 372, 27 Oct 2026): the toast also keeps the words said once undone.
    expect(view).toContain('if (undo) setToast({ message, undo, undone: "Moved back." });');
    expect(view).toContain("<Toast");
  });

  it("says where they went, and how many stayed", () => {
    expect(movedMessage(1, 1, "Invoices")).toBe("Moved to Invoices.");
    expect(movedMessage(3, 3, "Suppliers / 2026")).toBe("Moved 3 documents to Suppliers / 2026.");
    expect(movedMessage(2, 3, "Invoices", "This document is in Trash.")).toBe(
      "Moved 2 documents to Invoices. Not moved: 1 document. This document is in Trash.",
    );
  });
});
