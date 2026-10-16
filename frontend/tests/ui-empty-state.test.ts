// The shared empty state (board 12 P6): two ruled paper sheets as quiet decoration,
// hidden from screen readers, drawn in tokens, never red, never moving; the words unchanged.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EmptyState } from "@/components/ui/EmptyState";

const html = renderToStaticMarkup(
  createElement(EmptyState, { icon: "documents", title: "No documents yet", action: createElement("button", null, "Upload files") }, "Upload invoices."),
);
const css = readFileSync(join(__dirname, "..", "components/ui/EmptyState.module.css"), "utf8");

describe("EmptyState", () => {
  it("draws two ruled sheets, hidden and unfocusable, with the screen's icon on them", () => {
    const art = /<span class="[^"]*art[^"]*" aria-hidden="true">([\s\S]*?)<\/svg><\/span><\/span>/.exec(html)?.[1] ?? "";
    expect(art).toContain('focusable="false"');
    expect(art.match(/<rect /g)).toHaveLength(2); // the two sheets (the documents icon draws paths only)
    expect(art.match(/h32|h48/g)?.length).toBeGreaterThanOrEqual(8); // ruled lines on both
    expect(html.match(/<svg/g)).toHaveLength(2); // the sheets and the icon, both inside the hidden span
  });

  it("keeps the heading, the words and the action as they were", () => {
    expect(html).toMatch(/<h2 class="[^"]*">No documents yet<\/h2>/);
    expect(html).toContain("Upload invoices.");
    expect(html).toContain("<button>Upload files</button>");
  });

  it("colours the sheets with tokens only, never red, and never moves", () => {
    expect(html).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(|fill="(?!none)|stroke="(?!currentColor)/i);
    expect(css).toMatch(/\.sheet rect \{[^}]*fill: var\(--surface\)/);
    expect(css).not.toMatch(/--brand-lal|--danger|--illegible|--review|#[0-9a-f]{3,8}\b/i);
    expect(css).not.toMatch(/animation|transition/);
  });
});
