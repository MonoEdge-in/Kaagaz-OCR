// Tushar, 17 Oct 2026 ("ui is unsymmetric"): every row of the documents table lines up.
// "Review" starts at the same spot on each row, "Land record" sits on the line of "7 flagged",
// and "Image (JPEG)" stays on one line, so no row is taller than the others.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "..", "components/documents/Documents.module.css"), "utf8");

describe("the documents table lines up", () => {
  // Changed on purpose (owner, 29 Oct 2026, #452): "2 pages to…" was cut beside Review. Review
  // ends its line, so it is in one spot on every row; the count before it is short enough to fit.
  it("puts Review at the end of the status's second line, one size on every row", () => {
    expect(css).toMatch(/\.statusAction \{[^}]*flex: none;\s*margin-inline-start: auto;\s*padding-inline: var\(--space-2\);/);
  });

  // Changed on purpose (owner, 29 Oct 2026, #452): "PDF · Land record" is the Type cell's first
  // line, cut with "…" (its whole words in its title), and the size its second.
  it("puts the file type and the document kind on one line, the size under them", () => {
    expect(css).toMatch(/\.docType::before \{\s*content: "·";/);
    // Changed on purpose (review panel, 29 Oct 2026): in the one rule for lines cut with "…".
    expect(css).toMatch(/\.typeLine,\s*\.bandBy,\s*\.byName \{\s*overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;/);
    expect(css).toMatch(/\.oneLine,\s*\.typeLine \{\s*display: block;/);
  });

  it("keeps the type on one line", () => {
    expect(css).toMatch(/\.typeColumn \{\s*width: 8\.5rem;/);
  });
});
