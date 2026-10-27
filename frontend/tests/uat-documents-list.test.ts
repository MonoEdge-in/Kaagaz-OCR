// Vrushit's UAT 2 (27 Oct 2026), the documents list and the shell, one block an issue.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FolderNav } from "@/components/documents/FolderNav";
import { buildTree } from "@/components/documents/folderTree";
import { ALL_DOCUMENTS, NO_DEPARTMENT } from "@/components/documents/libraryQuery";
import type { LibraryState } from "@/components/documents/libraryQuery";
import type { Folder } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");

const LAND = "11111111-1111-4111-8111-111111111111";
const OLD = "22222222-2222-4222-8222-222222222222";
const folder = (id: string, name: string, kind: Folder["kind"]): Folder => ({
  id,
  name,
  parent_id: null,
  created_at: "2026-10-01T10:00:00Z",
  kind,
  sort_order: kind === "department" ? 0 : null,
});
const TREE = buildTree([folder(LAND, "Land & Legal", "department"), folder(OLD, "Old scans", "custom")]);

function nav(library: LibraryState): string {
  return renderToStaticMarkup(
    createElement(FolderNav, {
      tree: TREE,
      library,
      loading: false,
      error: null,
      onRetry: () => undefined,
      onNavigate: () => undefined,
    }),
  );
}

/** The <a> whose words are `words`, as rendered. */
const link = (html: string, words: string) => html.match(new RegExp(`<a [^>]*>(?:(?!</a>).)*${words}</a>`))?.[0] ?? "";

describe("408: a row's Move to Trash asks in a dialog, not over the row", () => {
  const table = read("components/documents/DocumentsTable.tsx");

  it("asks in the house dialog, as the document screen does, with Cancel focused", () => {
    expect(table).toMatch(
      /<ActionDialog\s+title=\{confirming === "forever" \? "Delete forever" : "Move to Trash"\}[\s\S]*?canClose=\{\(\) => !busy\}/,
    );
    expect(table).toMatch(/onClosed=\{\(\) => actionsButton\.current\?\.focus\(\)\}/);
    expect(table).toMatch(/className="btn" data-autofocus onClick=\{cancel\} disabled=\{busy\}>\s*Cancel/);
    expect(table).not.toContain("styles.confirm");
  });

  it("keeps the menu button in its row while it asks, so the row does not change under it", () => {
    expect(table).toMatch(/\{confirming === null && busy \? \([\s\S]*?\{working\}[\s\S]*?\) : \(\s*<RowMenu/);
  });

  it("says Starting… while a row's Retry runs, and Restoring… only for Restore (#407)", () => {
    expect(table).toMatch(/async function retry\(\) \{\s*setWorking\(BUSY_WORDS\.read\);/);
    expect(table).toMatch(/action === "restore"\) \{\s*setWorking\(BUSY_WORDS\.restore\);/);
    expect(table).not.toMatch(/\/> Restoring…/);
  });

  it("no longer draws a panel placed over the row's columns", () => {
    expect(css("components/documents/Documents.module.css")).not.toMatch(/\.confirm\s*\{/);
  });

  it("never opens the document for a click inside a row's dialog", () => {
    expect(table).toMatch(/closest\("a, button, input, label, select, textarea, dialog, /);
  });
});

describe("409: Other in the folder tree opens the documents in no department", () => {
  it("is a link to the list's no-department filter, as the dashboard's Not in a department is", () => {
    expect(link(nav(ALL_DOCUMENTS), "Other")).toContain('href="/documents?dept=none"');
  });

  it("is marked as the place being viewed there, and All documents is not", () => {
    const html = nav({ ...ALL_DOCUMENTS, departmentId: NO_DEPARTMENT });
    expect(link(html, "Other")).toContain('aria-current="page"');
    expect(link(html, "All documents")).not.toContain("aria-current");
    expect(link(nav(ALL_DOCUMENTS), "Other")).not.toContain("aria-current");
  });

  it("is left by going anywhere else in the tree, and other filters stay", () => {
    const html = nav({ ...ALL_DOCUMENTS, departmentId: NO_DEPARTMENT, type: "pdf" });
    expect(link(html, "All documents")).toContain('href="/documents?type=pdf"');
    expect(link(html, "Old scans")).toContain(`href="/documents?folder=${OLD}&amp;type=pdf"`);
    expect(link(html, "Trash")).toContain('href="/documents?trash=1&amp;type=pdf"');
    // A department picked in the filters is a filter, not the place: it stays.
    expect(link(nav({ ...ALL_DOCUMENTS, departmentId: LAND }), "Old scans")).toContain(`dept=${LAND}`);
  });

  it("names the list it opens after the files it holds", () => {
    expect(read("components/documents/DocumentsView.tsx")).toMatch(
      /library\.departmentId === NO_DEPARTMENT\s*\?\s*FILES_TEXT\.notInDepartment/,
    );
  });
});

describe("420: the Uploads button sits inside the status bar with room around it", () => {
  const rule = (path: string, selector: string) =>
    css(path).match(new RegExp(`(?:^|\\n)${selector.replace(".", "\\.")} \\{([^}]*)\\}`))?.[1] ?? "";
  const px = (token: string) => Number(read("app/globals.css").match(new RegExp(`${token}: (\\d+)px;`))?.[1]);

  it("is shorter than the bar, so it no longer touches its edges", () => {
    const toggle = rule("components/documents/Documents.module.css", ".barToggle");
    const height = toggle.match(/\bheight: var\((--[\w-]+)\);/)?.[1] ?? "";
    expect(height).toBe("--badge-h");
    expect(px("--statusbar-h") - px(height)).toBeGreaterThanOrEqual(8); // 4px or more above and below
  });

  it("keeps its words clear of its own edge", () => {
    expect(rule("components/documents/Documents.module.css", ".barToggle")).toMatch(/padding-inline: var\(--space-3\);/);
  });
});
