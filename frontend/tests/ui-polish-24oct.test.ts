// UI fixes of 24 Oct 2026 (Tushar): the document's actions in one "⋯" menu, the Document card
// gone, the checker offered only when it read text, one Attention box with the page's fix in
// it, a box pressed on the page showing its line in Text, the AI engine switch as a rounded
// pill, the Documents list's lines under the name's words, and the Upload card's picker lined up.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { lineHolding } from "@/components/viewer/scanned";
import type { Region } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const VIEW = "app/(app)/documents/[id]/DocumentView.tsx";
const box = (page: number, y: number, height = 0.04): Region => ({ kind: "box", page, box: [0.1, y, 0.9, y + height] });

describe("a box pressed on the page shows its line in Text", () => {
  it("finds the line the box overlaps most, on its own page only", () => {
    const lines = [{ region: box(1, 0.1) }, { region: box(1, 0.2) }, { region: box(1, 0.3) }, { region: box(2, 0.2) }];
    const word: Region = { kind: "box", page: 1, box: [0.5, 0.205, 0.6, 0.235] };
    expect(lineHolding(lines, word)).toBe(1);
    expect(lineHolding(lines, { kind: "box", page: 3, box: [0.5, 0.2, 0.6, 0.23] })).toBe(-1);
    expect(lineHolding(lines, null)).toBe(-1);
  });

  it("lights and scrolls to that line, and a word box keeps Text open instead of starting a check", () => {
    const panel = read("components/viewer/ScannedTextPanel.tsx");
    expect(panel).toContain("const holding = lineHolding(lines, active);");
    expect(panel).toContain('scrollIntoView({ block: "nearest", behavior: calm ? "auto" : "smooth" })');
    expect(panel).toContain("holding >= FIRST_LINES ? lines");
    const view = read(VIEW);
    expect(view).toMatch(/const pressFlag = \(flag: WordFlag\) => \{\s*if \(tab === "scanned"\) \{/);
    expect(view.match(/onFlag=\{pressFlag\}/g)).toHaveLength(2);
    expect(css("components/viewer/ScannedText.module.css")).toMatch(/\.held \{[^}]*background: var\(--bg-selected\);/);
  });
});

describe("the document page", () => {
  it("offers the checker only when it read text", () => {
    expect(read("components/viewer/ScannedTextPanel.tsx")).toContain(
      'const hasSecond = readers.some((r) => r.role === "second" && r.lines.length > 0);',
    );
  });

  it("keeps Ensemble, Export and Download original in the one ⋯ menu, never a More of the status bar", () => {
    expect(read(VIEW)).toContain("more={documentMoreItems(review.status, {");
    expect(read("app/(app)/documents/[id]/WordReview.tsx")).not.toContain("<RowMenu");
    expect(read("components/documents/DocumentActions.tsx")).toContain("{more.map((extra, index) => (");
  });

  it("puts the page's fix inside the Attention box: one box, the reason said once", () => {
    const view = read(VIEW);
    expect(view).toMatch(/<AttentionPanel\s+fix=\{\s*page && \(\s*<PageProblems[\s\S]*?inline/);
    const problems = read("app/(app)/documents/[id]/PageProblems.tsx");
    expect(problems).toContain('{inline ? `Fix page ${page.page_no}` : `Page ${page.page_no} is flagged for review`}');
    expect(problems).toMatch(/\{inline \? null : \(\s*<>\s*<span className=\{styles\.problemSays\}>/);
  });
});

describe("the rest of the screens", () => {
  it("shows the AI engine as a rounded switch, the chosen one a raised pill in the accent", () => {
    const chat = css("components/chat/Chat.module.css");
    expect(chat).toMatch(/\.segments \{[^}]*border-radius: var\(--k-r-pill\);/);
    expect(chat).toMatch(/\.segment:has\(input:checked\) \{[^}]*color: var\(--accent\);[^}]*box-shadow: var\(--k-shadow-1\);/);
    expect(chat).not.toMatch(/\.segment:has\(input:checked\) \{[^}]*outline:/);
  });

  it("starts the lines under a document's name under its words, and Review is a small button", () => {
    const docs = css("components/documents/Documents.module.css");
    expect(docs).toMatch(/\.nameCell \{\s*--name-indent: calc\(24px \+ var\(--space-2\)\);/);
    // Changed on purpose (issue 371, 27 Oct 2026): "In <folder>" is on the facts' line, indented with it.
    // Changed on purpose (owner, 29 Oct 2026, #452): the narrowest table's type line sits inside
    // the facts' line, so it is indented with it and has no indent of its own.
    expect(docs).toMatch(/\.facts,\s*\.newerVersion,\s*\.nameCell > ul \{\s*margin-inline-start: var\(--name-indent\);/);
    expect(docs).toMatch(/\.statusAction \{[^}]*border-radius: var\(--k-r-pill\);/);
  });

  it("lines the Upload card's department up with its text", () => {
    expect(css("components/dashboard/Dashboard.module.css")).toMatch(/\.uploadTo > \* \{[^}]*padding-inline-start: 0;/);
  });
});
