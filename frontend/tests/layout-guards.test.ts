// Layout rules for the tables (council 2026-10-04, responsive screens). Each
// guard reads the CSS source, so a rule that brings an old bug back fails here:
// text that cannot wrap ("29 minutes agoTushar"), words broken anywhere
// ("Unknow|n"), and columns that need more room than the table has.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

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

describe("layout guards", () => {
  it("lets every table cell and its text wrap (a header may stay on one line)", () => {
    expect(nowrapCells([DOCS_CSS])).toEqual([]);
  });

  it("never breaks words anywhere in a table cell, only where they are too long to fit", () => {
    const offenders = rules(read(DOCS_CSS))
      .filter((rule) => /overflow-wrap:\s*anywhere/.test(rule.body))
      .flatMap(selectors)
      .filter((s) => CELL.test(s));
    expect(offenders).toEqual([]);
  });

  it("keeps the documents table free of one-line cells in its markup", () => {
    expect(read("components/documents/DocumentsTable.tsx")).not.toMatch(/styles\.nowrap/);
  });

  it("asks container queries in rem, so the switch follows the text size", () => {
    const px = rules(read(DOCS_CSS))
      .map((rule) => rule.at)
      .filter((at) => at.includes("@container") && /\d+(\.\d+)?px/.test(at));
    expect(px).toEqual([]);
  });

  it("gives the documents table's short columns rem widths, never percentages", () => {
    const percent = rules(read(DOCS_CSS))
      .filter((rule) => selectors(rule).some((s) => /Column$/.test(s)) && /(?<![-\w])width:\s*[\d.]+%/.test(rule.body))
      .map((rule) => rule.selector);
    expect(percent).toEqual([]);
  });

  it("leaves Name and Status room at each width the documents table starts at", () => {
    const found = budgets(DOCS_CSS, "docs-list", NAME_AND_STATUS_REM);
    expect(found.map((b) => b.start)).toEqual([48, 64]);
    for (const { start, needs } of found) expect(needs, `at ${start}rem`).toBeLessThanOrEqual(start);
  });
});
