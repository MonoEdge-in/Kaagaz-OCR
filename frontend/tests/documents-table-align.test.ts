// Tushar, 17 Oct 2026 ("ui is unsymmetric"): every row of the documents table lines up.
// "Review" starts at the same spot on each row, "Land record" sits on the line of "7 flagged",
// and "Image (JPEG)" stays on one line, so no row is taller than the others.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "..", "components/documents/Documents.module.css"), "utf8");

describe("the documents table lines up", () => {
  it("gives the text before Review one fixed width", () => {
    expect(css).toMatch(/\.statusText:has\(\+ \.statusAction\) \{\s*flex: 0 1 9rem;/);
  });

  it("puts the document kind on the status's second line", () => {
    // Changed on purpose (Vrushit UAT, 25 Oct 2026): the status's second line is --row-sub too.
    expect(css).toMatch(/\.docType \{\s*line-height: var\(--row-sub\);/);
    expect(css).toMatch(/table\.docs \.docType \{\s*line-height: var\(--h-lg\);/);
  });

  it("keeps the type on one line", () => {
    expect(css).toMatch(/\.typeColumn \{\s*width: 8\.5rem;/);
    expect(css).toMatch(/table\.docs \.docType \{\s*overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;/);
  });
});
