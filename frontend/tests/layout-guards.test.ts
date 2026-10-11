// Layout rules for the tables (council 2026-10-04, responsive screens). Each
// guard reads the CSS source, so a rule that brings an old bug back fails here:
// text that cannot wrap ("29 minutes agoTushar"), words broken anywhere
// ("Unknow|n"), and columns that need more room than the table has.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { STICKY_VIEWER_QUERY } from "@/components/viewer/viewer";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

interface Rule {
  /** The at-rule preludes around the rule, outermost first ("" at the top level). */
  at: string;
  selector: string;
  body: string;
}

/** The style rules of a CSS file. Enough for this repo: no nesting, no braces in strings. */
function rules(css: string): Rule[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Rule[] = [];
  const stack: string[] = [];
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === "{") {
      const prelude = text.slice(start, i).trim();
      if (prelude.startsWith("@")) {
        stack.push(prelude);
        start = i + 1;
        continue;
      }
      const end = text.indexOf("}", i);
      out.push({ at: stack.join(" "), selector: prelude, body: text.slice(i + 1, end) });
      i = end;
      start = end + 1;
    } else if (text[i] === "}") {
      stack.pop();
      start = i + 1;
    }
  }
  return out;
}

const selectors = (rule: Rule) => rule.selector.split(",").map((part) => part.trim());

// Cells and the text inside them must be able to wrap; a header may stay on one line.
const WRAPPING = /\b(td|th)\b|Cell\b|Message\b|\.meta\b|\.name\b|\.statusContent\b/;
const NOWRAP = /white-space:\s*nowrap/;
const CELL = /\b(td|th)\b/;

/** Rules in these files whose cells or cell text are kept on one line. */
function nowrapCells(files: string[]): string[] {
  return files.flatMap((file) =>
    rules(read(file))
      .filter((rule) => NOWRAP.test(rule.body))
      .flatMap((rule) => selectors(rule).filter((s) => WRAPPING.test(s) && !s.includes("thead")))
      .map((s) => `${file}: ${s}`),
  );
}

/** Where a container query applies, in rem: [min, max]. null when it is not that container. */
function containerRange(at: string, container: string): [number, number] | null {
  const query = new RegExp(`@container ${container}\\b([^@]*)`).exec(at)?.[1];
  if (query === undefined) return null;
  const min = /min-width:\s*([\d.]+)rem/.exec(query)?.[1];
  const max = /max-width:\s*([\d.]+)rem/.exec(query)?.[1];
  return [min ? Number(min) : 0, max ? Number(max) : Infinity];
}

/** The widths (rem) of the columns shown when the container is `width` rem wide. */
function columnsAt(css: string, container: string, width: number): Map<string, number> {
  const widths = new Map<string, number>();
  const hidden = new Set<string>();
  for (const rule of rules(css)) {
    const range = containerRange(rule.at, container);
    if (!range || width < range[0] || width > range[1]) continue;
    for (const column of selectors(rule).map((s) => /\.(\w+Column)$/.exec(s)?.[1])) {
      if (!column) continue;
      if (/display:\s*none/.test(rule.body)) hidden.add(column);
      const rem = /(?<![-\w])width:\s*([\d.]+)rem/.exec(rule.body)?.[1];
      if (rem) widths.set(column, Number(rem));
    }
  }
  for (const column of hidden) widths.delete(column);
  return widths;
}

/** The least room Name and Status share in the documents table (11rem each). */
const NAME_AND_STATUS_REM = 22;

/** The least room the admin Users table's User column (name and e-mail) keeps. */
const USER_REM = 11;

/** Each width a table's container queries start at, with the columns shown there. */
function budgets(file: string, container: string, keep: number) {
  const css = read(file);
  const starts = new Set(
    rules(css)
      .map((rule) => containerRange(rule.at, container)?.[0])
      .filter((min): min is number => min !== undefined && min > 0),
  );
  return [...starts]
    .sort((a, b) => a - b)
    .map((start) => {
      const columns = [...columnsAt(css, container, start).values()];
      return { start, needs: columns.reduce((sum, rem) => sum + rem, keep) };
    });
}

const DOCS_CSS = "components/documents/Documents.module.css";
const ADMIN_CSS = "components/admin/Admin.module.css";
const OPERATOR_CSS = "components/operator/Operator.module.css";
const PLATFORM_CSS = "components/operator/Platform.module.css";
const TABLE_FILES = [DOCS_CSS, "components/documents/Table.module.css", ADMIN_CSS, OPERATOR_CSS, PLATFORM_CSS];

/** The rules for exactly this selector (one of a list counts), in a CSS file. */
const rulesFor = (file: string, selector: string) =>
  rules(read(file)).filter((rule) => selectors(rule).includes(selector));

describe("layout guards", () => {
  it("lets every table cell and its text wrap (a header may stay on one line)", () => {
    expect(nowrapCells(TABLE_FILES)).toEqual([]);
  });

  it("never breaks words anywhere in a table cell, only where they are too long to fit", () => {
    const offenders = TABLE_FILES.flatMap((file) =>
      rules(read(file))
        .filter((rule) => /overflow-wrap:\s*anywhere/.test(rule.body))
        .flatMap(selectors)
        .filter((s) => CELL.test(s))
        .map((s) => `${file}: ${s}`),
    );
    expect(offenders).toEqual([]);
  });

  it("keeps the tables free of one-line cells in their markup", () => {
    const markup = [
      "components/documents/DocumentsTable.tsx",
      "components/admin/UsersPanel.tsx",
      "components/admin/ActivityPanel.tsx",
      "components/admin/FailedPanel.tsx",
      "components/operator/OrganisationsView.tsx",
      "components/operator/ConfirmDialog.tsx",
      "components/operator/CreateDialog.tsx",
      "components/operator/AdminsDialog.tsx",
      "components/operator/ActivityView.tsx",
      "components/operator/HealthView.tsx",
    ];
    expect(markup.filter((file) => /styles\.nowrap/.test(read(file)))).toEqual([]);
  });

  it("asks container queries in rem, so the switch follows the text size", () => {
    const px = TABLE_FILES.flatMap((file) => rules(read(file)).map((rule) => rule.at))
      .filter((at) => at.includes("@container") && /\d+(\.\d+)?px/.test(at));
    expect(px).toEqual([]);
  });

  it("gives the tables' short columns rem widths, never percentages", () => {
    const percent = TABLE_FILES.flatMap((file) => rules(read(file)))
      .filter((rule) => selectors(rule).some((s) => /Column$/.test(s)) && /(?<![-\w])width:\s*[\d.]+%/.test(rule.body))
      .map((rule) => rule.selector);
    expect(percent).toEqual([]);
  });

  it("leaves Name and Status room at each width the documents table starts at", () => {
    const found = budgets(DOCS_CSS, "docs-list", NAME_AND_STATUS_REM);
    expect(found.map((b) => b.start)).toEqual([48, 64]);
    for (const { start, needs } of found) expect(needs, `at ${start}rem`).toBeLessThanOrEqual(start);
  });

  it("lets a badge wrap, so a long label never pushes the page sideways", () => {
    const badge = rulesFor("app/globals.css", ".badge");
    expect(badge.length).toBeGreaterThan(0);
    expect(badge.filter((rule) => NOWRAP.test(rule.body))).toEqual([]);
  });

  it("stretches the sidebar down the page; only its inner part sticks", () => {
    const shell = "components/shell/Shell.module.css";
    expect(rulesFor(shell, ".sidebar").filter((rule) => /position:\s*sticky/.test(rule.body))).toEqual([]);
    expect(rulesFor(shell, ".sidebarInner").some((rule) => /position:\s*sticky/.test(rule.body))).toBe(true);
  });

  it("gives the page frame no height limit of its own (no second scrollbar)", () => {
    const frame = rulesFor("components/viewer/PageViewer.module.css", ".frame");
    expect(frame.length).toBeGreaterThan(0);
    expect(frame.filter((rule) => /max-height/.test(rule.body))).toEqual([]);
  });

  it("rings the highlight in paper then dark ink, in both themes (1.4.11)", () => {
    const box = rulesFor("components/viewer/PageViewer.module.css", ".box");
    expect(box.map((rule) => /box-shadow:([^;]*)/.exec(rule.body)?.[1]?.trim())).toEqual([
      "0 0 0 2px var(--paper), 0 0 0 4px var(--hl-halo)",
    ]);
    // Set once, so the dark theme keeps it; a dark ink, never transparent.
    const halo = read("app/globals.css").match(/--hl-halo:\s*([^;]+);/g) ?? [];
    expect(halo).toEqual(["--hl-halo: #1f1a14;"]);
    const channel = (hex: string) => {
      const value = parseInt(hex, 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    };
    const ink = 0.2126 * channel("1f") + 0.7152 * channel("1a") + 0.0722 * channel("14");
    expect((1 + 0.05) / (ink + 0.05)).toBeGreaterThanOrEqual(3); // against the white paper ring
  });

  it("pulses a new spot only where reduced motion can stop it", () => {
    const css = read("components/viewer/PageViewer.module.css");
    expect(rulesFor("components/viewer/PageViewer.module.css", ".pulse")[0]?.body).toMatch(/animation:\s*spotPulse [\d.]+s ease-out 2;/);
    expect(css).toContain("@keyframes spotPulse");
    const reduced = rules(read("app/globals.css")).filter((rule) => rule.at === "@media (prefers-reduced-motion: reduce)");
    expect(reduced.some((rule) => /animation-iteration-count:\s*1 !important/.test(rule.body))).toBe(true);
  });

  it("sticks the page column under the same media query the page fit uses", () => {
    const sticky = rules(read("app/(app)/documents/[id]/DocumentView.module.css")).filter(
      (rule) => selectors(rule).includes(".viewer") && /position:\s*sticky/.test(rule.body),
    );
    expect(sticky.map((rule) => rule.at)).toEqual([`@media ${STICKY_VIEWER_QUERY}`]);
  });

  it("leaves the admin Users table's User column room where it becomes a table", () => {
    const found = budgets(ADMIN_CSS, "reflow", USER_REM);
    expect(found.map((b) => b.start)).toEqual([48]);
    for (const { start, needs } of found) expect(needs, `at ${start}rem`).toBeLessThanOrEqual(start);
  });

  it("leaves the organisations table's Name column room where it is a table", () => {
    const css = read(OPERATOR_CSS);
    for (const width of [48, 64]) {
      const needs = [...columnsAt(css, "reflow", width).values()].reduce((sum, rem) => sum + rem, USER_REM);
      expect(needs, `at ${width}rem`).toBeLessThanOrEqual(width);
    }
    expect(columnsAt(css, "reflow", 48).has("pagesColumn")).toBe(false);
    expect(columnsAt(css, "reflow", 64).has("pagesColumn")).toBe(true);
  });

  it("leaves the activity and health tables' words columns 11rem each where they are tables", () => {
    const widths = columnsAt(read(PLATFORM_CSS), "reflow", 48);
    const sum = (columns: string[]) => columns.reduce((total, column) => total + (widths.get(column) ?? 0), 0);
    // Activity: Organisation and Action share the rest; Health: Name, Documents and Failed.
    expect(sum(["whenColumn", "targetColumn", "byColumn"]) + 2 * USER_REM).toBeLessThanOrEqual(48);
    expect(sum(["statusColumn", "waitColumn"]) + 3 * USER_REM).toBeLessThanOrEqual(48);
  });

  it("lets a status chip wrap inside its cell", () => {
    const chip = rules(read("app/globals.css")).filter((rule) => selectors(rule).includes(".status-chip"));
    expect(chip.length).toBeGreaterThan(0);
    expect(chip.filter((rule) => NOWRAP.test(rule.body))).toEqual([]);
  });
});
