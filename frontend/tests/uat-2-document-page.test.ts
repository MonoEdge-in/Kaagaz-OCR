// Vrushit's second UAT of the document page (27 Oct 2026), one block an issue. The removal of
// "Your words in this document" (#412) is in page-words.test.ts.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const VIEW_CSS = "app/(app)/documents/[id]/DocumentView.module.css";

describe("#415 the summary card's Show the full summary sits inside the card", () => {
  it("keeps the button's own side padding, so its pill never runs past the card's text edge", () => {
    const rule = /\n\.expand \{([^}]*)\}/.exec(css(VIEW_CSS))?.[1] ?? "";
    expect(rule).toContain("align-self: flex-start;");
    expect(rule).not.toMatch(/padding/);
    expect(rule).not.toMatch(/margin[^:]*:\s*-/);
    expect(read("app/(app)/documents/[id]/SummaryCard.tsx")).toContain("className={`btn btn-quiet ${styles.expand}`}");
  });
});
