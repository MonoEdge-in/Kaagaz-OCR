// Owner's report (29 Oct 2026, a Marathi land record in the Text tab, dark, Plum): the line
// numbers were gone and a line's first glyph could be cut; in the open fix box, Save was cut off
// at the panel's edge; and the open row's Add meaning showed as an outlined pill (the hover of a
// button that slid under the pointer when Fix text stepped aside). Seen in Chromium: the <li>'s
// content-visibility clips its paint to the item, and the numbers are drawn outside it.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const sheet = read("components/viewer/ScannedText.module.css").replace(/\/\*[\s\S]*?\*\//g, "");
const panel = read("components/viewer/ScannedTextPanel.tsx");
/** The body of the one rule whose selector is exactly this. */
const rule = (selector: string) => {
  const found = [...sheet.matchAll(/([^{}]+)\{([^}]*)\}/g)].filter((m) => m[1]?.trim() === selector);
  expect(found, selector).toHaveLength(1);
  return found[0]?.[2] ?? "";
};

describe("a scanned line is never clipped", () => {
  it("keeps the rendering skip off the <li>, on the row inside it, so the item clips nothing", () => {
    expect(rule(".line")).not.toMatch(/content-visibility|contain/);
    expect(rule(".lineRow")).toMatch(/content-visibility: auto;\s*contain-intrinsic-size: auto 44px;/);
    expect(sheet.match(/content-visibility/g)).toHaveLength(1);
  });

  it("draws each line's number in the list's margin, level with its first line of text", () => {
    expect(rule(".lines")).toMatch(/padding-inline-start: 3\.5em;[\s\S]*list-style: none;\s*counter-reset: line;/);
    expect(rule(".line")).toMatch(/position: relative;[\s\S]*counter-increment: line;/);
    const number = rule(".line::before");
    expect(number).toMatch(/content: counter\(line\) "\.";/);
    expect(number).toMatch(/position: absolute;\s*inset-block-start: var\(--space-1\);\s*inset-inline-end: 100%;/);
    expect(number).toMatch(/white-space: nowrap;\s*line-height: calc\(var\(--fs-base\) \* 1\.6\);/);
    // The line button's own top padding and the read text's line height (.readText, 1.6).
    expect(rule(".lineButton")).toMatch(/padding: var\(--space-1\) var\(--space-2\);/);
    // Still a list to a screen reader in Safari, which drops a list drawn without markers.
    expect(panel).toContain('<ol className={styles.lines} role="list">');
  });
});

describe("the open fix box", () => {
  it("starts its words where the line's do", () => {
    expect(rule(".fix")).toMatch(/padding: var\(--space-2\) var\(--space-2\) var\(--space-3\);/);
  });

  // Changed on purpose (PRD 9 F-13, D6): Cancel and Save only; Save keeps its group.
  it("gives each button its own width and wraps them, never cut off", () => {
    expect(rule(".fixActions,\n.fixSettle")).toMatch(/display: flex;\s*flex-wrap: wrap;\s*justify-content: flex-end;/);
    expect(rule(".fixActions")).toMatch(/^\s*flex-wrap: wrap-reverse;\s*$/); // Save on top
    expect(sheet).not.toMatch(/\.fix\w*[^{]*\{[^}]*\bflex: 1/); // nothing stretched to share the row
    expect(sheet).not.toContain(":global(.btn)");
    expect(panel).toMatch(
      /<div className=\{styles\.fixActions\}>\s*<button type="button" className="btn btn-quiet" onClick=\{\(\) => closeFix\(index\)\}>\s*Cancel\s*<\/button>\s*<div className=\{styles\.fixSettle\}>\s*<button type="submit" className="btn btn-primary"[^>]*>\s*Save\s*<\/button>\s*<\/div>\s*<\/div>/,
    );
  });

  it("is its row's one task: Add meaning steps aside while it is open, and comes back after", () => {
    expect(panel).toContain("{onAddMeaning && !box && (");
    expect(panel).toContain("{toFix && ask && !box && (");
  });
});
