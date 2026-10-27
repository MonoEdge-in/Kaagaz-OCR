// Vrushit's UAT 2 (27 Oct 2026), the documents list and the shell, one block an issue.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");

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
    expect(table).toMatch(/\{confirming === null && busy \? \([\s\S]*?Restoring…[\s\S]*?\) : \(\s*<RowMenu/);
  });

  it("no longer draws a panel placed over the row's columns", () => {
    expect(css("components/documents/Documents.module.css")).not.toMatch(/\.confirm\s*\{/);
  });

  it("never opens the document for a click inside a row's dialog", () => {
    expect(table).toMatch(/closest\("a, button, input, label, select, textarea, dialog, /);
  });
});
