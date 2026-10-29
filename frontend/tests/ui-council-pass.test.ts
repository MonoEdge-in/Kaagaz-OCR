// The UI council's pass (Tushar, 24 Oct 2026: "corners round to match the product, smoothness
// and animation top priority, button colours, strokes, filters and transitions the best they
// can be"). These hold what it settled: hover only with a mouse, round containers and pill
// controls, one neutral hover, press feedback on every toggle, details easing open, loading
// lines that never flash, and a quicker Home entrance.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const files = (dir: string): string[] =>
  readdirSync(join(ROOT, dir)).flatMap((name) => {
    const path = `${dir}/${name}`;
    return statSync(join(ROOT, path)).isDirectory() ? files(path) : name.endsWith(".css") ? [path] : [];
  });
const CSS = [...files("app"), ...files("components")];

/** Top-level rules (not inside any @-block): [selector, body]. */
function topLevel(text: string): [string, string][] {
  const out: [string, string][] = [];
  let depth = 0;
  let start = 0;
  let open = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === "{") {
      if (depth === 0) open = i;
      depth += 1;
    } else if (text[i] === "}") {
      depth -= 1;
      if (depth === 0) {
        out.push([text.slice(start, open).trim(), text.slice(open + 1, i)]);
        start = i + 1;
      }
    } else if (text[i] === ";" && depth === 0) start = i + 1;
  }
  return out;
}

describe("the UI council's pass", () => {
  it("shows a hover only with a mouse: no top-level rule that is all :hover", () => {
    const found: string[] = [];
    for (const file of CSS) {
      for (const [selector] of topLevel(css(file))) {
        if (selector.startsWith("@") || !selector.includes(":hover")) continue;
        const parts = selector.split(",").map((part) => part.trim()).filter(Boolean);
        if (parts.every((part) => part.includes(":hover")) && !(file === "app/globals.css" && selector === "a:hover")) {
          found.push(`${file}: ${selector}`);
        }
      }
    }
    expect(found).toEqual([]);
  });

  it("rounds containers and makes small controls pills", () => {
    const rule = (file: string, selector: string) =>
      new RegExp(`(^|\\n)${selector.replace(/[.*+?^${}()|[\]\\>]/g, "\\$&")} \\{[^}]*border-radius: ([^;]+);`).exec(css(file))?.[2];
    expect(rule("components/documents/Documents.module.css", "table.docs")).toBe("var(--k-r-4)");
    expect(rule("components/documents/ActionDialog.module.css", ".dialog")).toBe("var(--k-r-5)");
    expect(rule("components/ui/ConfirmDialog.module.css", ".dialog")).toBe("var(--k-r-5)");
    expect(rule("components/documents/Library.module.css", ".menuList")).toBe("var(--k-r-4)");
    expect(rule("components/documents/Library.module.css", ".menuItem")).toBe("var(--k-r-3)");
    expect(rule("components/viewer/PageViewer.module.css", ".frame")).toBe("var(--k-r-4)");
    expect(rule("components/chat/Chat.module.css", ".chip")).toBe("var(--k-r-pill)");
    expect(rule("components/documents/Documents.module.css", ".badge")).toBe("var(--k-r-pill)");
    expect(rule("components/documents/Library.module.css", ".tag")).toBe("var(--k-r-pill)");
  });

  it("uses one neutral hover, never a grey that sinks in dark mode", () => {
    const found: string[] = [];
    for (const file of CSS) {
      for (const [, selector, body] of css(file).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        if ((selector ?? "").includes(":hover") && /background(-color)?:\s*var\(--(k-sunken|bg-subdued|surface-2)\)/.test(body ?? "")) {
          found.push(`${file}: ${(selector ?? "").trim()}`);
        }
      }
    }
    expect(found).toEqual([]);
  });

  it("gives every toggle a press, eases details open, and keeps the main button's lift to a mouse", () => {
    const globals = css("app/globals.css");
    expect(globals).toMatch(/button\[aria-pressed\]:active:not\(:disabled, \[aria-disabled="true"\]\),\s*\.k-chip:active,\s*\.k-suggest:active \{\s*transform: scale\(0\.97\);/);
    expect(globals).toMatch(/details\[open\] > :not\(summary\) \{\s*animation: details-in var\(--t-med\) var\(--ease\);/);
    expect(css("app/styles/components.css")).toContain(
      "@media (hover: hover) and (pointer: fine) { .k-btn--primary:hover { transform: translateY(-1px); } }",
    );
    expect(css("components/shell/Shell.module.css")).toMatch(/\.link:active,\s*\.bottomLink:active \{\s*transform: scale\(0\.98\);/);
  });

  it("never flashes a loading line on a fast load, and enters Home quicker", () => {
    for (const file of [
      "components/dashboard/Dashboard.tsx",
      "components/activity/Timeline.tsx",
      "components/admin/AnswerQualityPanel.tsx",
      "components/versions/CompareView.tsx",
      "components/operator/OrgModelRates.tsx",
    ]) {
      expect(read(file), file).toMatch(/className="(k-)?muted delayed" role="status"/);
    }
    expect(css("components/dashboard/Dashboard.module.css")).toContain("animation-delay: calc(var(--i, 0) * 40ms + 80ms);");
  });
});

describe("the UI council's responsiveness fixes", () => {
  it("never lets a phone zoom into a typing field, and keeps touch targets at 44px", () => {
    const globals = css("app/globals.css");
    expect(globals).toMatch(/@media \(max-width: 767\.98px\) \{\s*:root \{[^}]*--fs-field: max\(1rem, 16px\);/);
    expect(globals).toMatch(/\.input,\s*select\.input,\s*textarea\.input \{[^}]*font-size: var\(--fs-field\);/);
    expect(css("components/chat/Chat.module.css")).toMatch(/\.composerInput \{[^}]*font-size: var\(--fs-field\);/);
    expect(css("components/documents/Documents.module.css")).toMatch(/\.searchInput \{[^}]*font: 400 var\(--fs-field\)/);
    expect(css("components/dashboard/Dashboard.module.css")).toMatch(/\.askInput \{[^}]*font: 400 var\(--fs-field\)/);
    expect(css("app/styles/components.css")).toContain(
      "@media (pointer: coarse) { .k-btn, .k-btn--sm, .k-chip, .k-suggest { min-height: var(--h-lg); } }",
    );
    expect(css("components/shell/Shell.module.css")).toMatch(/@media \(pointer: coarse\) \{\s*\.link \{\s*min-height: var\(--h-lg\);/);
  });

  it("gives a phone a bigger page, two-line names on cards, and an icon Send on a small phone", () => {
    expect(css("components/viewer/PageViewer.module.css")).toMatch(/@media \(max-width: 639\.98px\) \{\s*\.frame \{\s*padding: var\(--space-1\);/);
    expect(css("components/documents/Documents.module.css")).toMatch(/@media \(max-width: 35\.98rem\) \{\s*\.name > span \{\s*-webkit-line-clamp: 2;/);
    expect(css("components/chat/Chat.module.css")).toMatch(/@media \(max-width: 479\.98px\) \{\s*\.send \{\s*width: var\(--control-h\);/);
    expect(css("components/shell/Shell.module.css")).toMatch(/@media \(max-width: 419\.98px\) \{\s*\.pageName \{\s*display: none;/);
  });
});

describe("the document page, 24 Oct 2026 (later)", () => {
  // Changed on purpose (Vrushit UAT 2, 27 Oct 2026, #416): Values found, with its AI note and
  // its scrolling list, is gone from the document page (uat-2-document-page.test.ts).

  it("gives the PDF viewer's bar the product's theme and pill controls", () => {
    expect(css("app/globals.css")).toMatch(/--viewer-bar: var\(--k-surface\);\s*--viewer-bar-ink: var\(--k-ink\);\s*--viewer-bar-field: var\(--k-sunken\);/);
    const viewer = read("components/extend/pdf-viewer.tsx");
    expect(viewer).not.toContain("bg-white/10");
    expect(viewer).toContain("[&_:is(button,a)]:rounded-full");
  });
});

describe("the Documents list's line rhythm", () => {
  it("gives every line after a row's first the same height, in every column", () => {
    const text = css("components/documents/Documents.module.css");
    const block = text.slice(text.lastIndexOf("@container docs-list (min-width: 36rem) {"));
    // Changed on purpose (Vrushit UAT, 25 Oct 2026): "keep it clustered, one line after one":
    // the same height in every column still, but 24px (--row-sub), not 32px.
    expect(block).toMatch(/\.facts \{\s*margin-block-start: 0;\s*line-height: var\(--row-sub\);/);
    expect(block).toMatch(/\.inFolder,\s*\.bandSize,\s*\.bandBy \{\s*line-height: var\(--row-sub\);/);
    // Changed on purpose (owner, 29 Oct 2026, #452): the kind is on the type's own first line
    // ("PDF · Land record") and the size is the Type cell's second line, --row-sub like the others.
    expect(text).toMatch(/@container docs-list \(min-width: 36rem\) \{[^@]*\.bandSize \{\s*display: block;/);
  });

  // Vrushit UAT (25 Oct 2026): "no vertical spacing and padding. Keep it clustered."
  it("stacks the lines under a name close, with a compact row padding", () => {
    const text = css("components/documents/Documents.module.css");
    expect(text).toMatch(/table\.docs \{\s*--row-line: var\(--control-h\);[^}]*--row-sub: var\(--badge-h\);/);
    expect(text).toMatch(/table\.docs td \{\s*height: 48px;\s*padding-block: var\(--space-2\);/);
    expect(text).not.toMatch(/\.(facts|inFolder|bandBy|docType|statusLine) \{[^}]*var\(--h-sm\)/);
  });
});

// Vrushit UAT (25 Oct 2026): the tags go in the Label column, after the label, not under the name.
describe("the Documents list's tags", () => {
  it("puts the tags with the label, under the name only while the Label column is hidden", () => {
    const table = read("components/documents/DocumentsTable.tsx");
    const label = table.slice(table.indexOf("styles.labelCell}"), table.indexOf("styles.versionCell}"));
    expect(label).toMatch(/<LabelChip label=\{document\.sensitivity\} \/> : dash\}\s*\{tagList\(\)\}/);
    expect(table).toContain("{tagList(styles.factTags)}");
    expect(table.match(/aria-label="Tags"/g)).toHaveLength(1); // one list, drawn in the two places
    const text = css("components/documents/Documents.module.css");
    expect(text).toMatch(/@container docs-list \(min-width: 56rem\) \{[^@]*\.factLabel,\s*\.factTags \{\s*display: none;/);
  });
});
