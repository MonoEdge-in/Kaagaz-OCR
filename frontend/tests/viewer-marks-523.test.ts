// #523: on a handwritten page the viewer's marks covered the writing. Marks point, never hide
// the text: an unsure word is a thin underline, the source highlight an outline with a light
// tint, "Show words to check" is on at first for reviewers and admins only, a page with more
// than 40 word marks shows a chip instead of them, and a stored word mark far taller than a
// word (a strip the height of a paragraph, read before the fix) is never drawn.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  WORD_MARK_CAP,
  showWordsByDefault,
  tallLimit,
  wordMarkView,
  wordsChipText,
} from "@/components/viewer/readingMarks";
import type { PageSummary, ReadingMark, Role } from "@/lib/types";

vi.mock("@/components/viewer/useObservedBox", () => ({
  useObservedBox: () => ({ ref: () => undefined, box: { width: 800, height: 1000 }, near: true }),
}));

/** A word mark `tall` of the page high, the `n`-th on its line of marks. */
const word = (n: number, tall = 0.02, quality: ReadingMark["quality"] = "unsure"): ReadingMark => ({
  quality,
  region: { kind: "box", page: 1, box: [0.05 + (n % 20) * 0.04, 0.1, 0.08 + (n % 20) * 0.04, 0.1 + tall] },
  text: `w${n}`,
});

const page: PageSummary = {
  page_no: 1,
  width: 595,
  height: 842,
  unit: "px",
  has_image: true,
  route: "ocr",
  effective_dpi: 300,
  low_resolution: false,
  read_by: "google/gemini-3-flash",
  read_quality: "uncertain",
  read_issues: ["handwriting_hard"],
  unsure_words: 1,
  illegible_words: 0,
  unpinned_lines: 0,
  accepted_by: null,
  accepted_at: null,
  read_again_requested_at: null,
};

async function render(readingMarks: ReadingMark[], role?: Role): Promise<string> {
  const { PageViewer } = await import("@/components/viewer/PageViewer");
  return renderToStaticMarkup(
    createElement(PageViewer, {
      document: { id: "d1", display_name: "Letter.jpg", detected_type: "jpeg" },
      page,
      highlights: [],
      readingMarks,
      role,
    }),
  );
}

const drawnWords = (html: string) => html.match(/<button [^>]*data-quality="(unsure|illegible)"[^>]*><\/button>/g) ?? [];

describe("how the marks look (#523)", () => {
  const css = readFileSync(join(process.cwd(), "components/viewer/PageViewer.module.css"), "utf8");
  const globals = readFileSync(join(process.cwd(), "app/globals.css"), "utf8");
  const rule = (selector: string) => css.match(new RegExp(`(?:^|\\n)${selector} \\{([^}]*)\\}`))?.[1] ?? "";

  it("draws an unsure word as a thin underline in the paper-fixed warning colour, with no box or fill", () => {
    const unsure = rule('\\.problem\\[data-quality="unsure"\\],\\n\\.swatch\\[data-quality="unsure"\\]');
    expect(unsure).toMatch(/border: 0;/);
    expect(unsure).toMatch(/border-bottom: 2px dashed var\(--warning-on-paper\);/);
    expect(unsure).not.toMatch(/background/);
    // The light theme's --warning, kept in dark too: the page is paper white in both themes.
    expect(globals.match(/--warning-on-paper: #[0-9a-f]+/g)).toEqual(["--warning-on-paper: #8a5800"]);
    const channel = (hex: string) => {
      const value = parseInt(hex, 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    };
    const ink = 0.2126 * channel("8a") + 0.7152 * channel("58") + 0.0722 * channel("00");
    expect((1 + 0.05) / (ink + 0.05)).toBeGreaterThanOrEqual(4.5); // 3:1 needed (1.4.11), 4.5:1 given
  });

  it("draws the source highlight as an outline with a tint of 12% at most, so the writing stays readable", () => {
    const box = rule("\\.box");
    expect(box).toMatch(/background: var\(--hl-tint\);/);
    expect(box).toMatch(/border: 2px solid var\(--hl-border\);/);
    const tint = globals.match(/--hl-tint: rgba\(255, 176, 0, ([0-9.]+)\)/)?.[1];
    expect(Number(tint)).toBeLessThanOrEqual(0.12);
  });
});

describe("Show words to check (#523)", () => {
  it("is on at first for reviewers and admins, off for members", () => {
    expect(showWordsByDefault("reviewer")).toBe(true);
    expect(showWordsByDefault("admin")).toBe(true);
    expect(showWordsByDefault("member")).toBe(false);
    expect(showWordsByDefault(undefined)).toBe(true); // a viewer told no role keeps its marks
  });

  it("is a switch in the toolbar, its word marks drawn only while it is on", async () => {
    const marks = [word(1), word(2, 0.02, "illegible"), { ...word(3), quality: "unpinned" as const }];
    const member = await render(marks, "member");
    expect(member).toMatch(/<button type="button" role="switch" aria-checked="false"[^>]*aria-label="Show words to check"/);
    expect(drawnWords(member)).toHaveLength(0);
    expect(member).toContain('data-quality="unpinned"'); // not a word: the switch leaves it alone
    const reviewer = await render(marks, "reviewer");
    expect(reviewer).toMatch(/role="switch" aria-checked="true"/);
    expect(drawnWords(reviewer)).toHaveLength(2);
    // Each drawn mark keeps its name for a screen reader.
    expect(drawnWords(reviewer)[0]).toContain(`aria-label="Unsure word &#x27;w1&#x27;, mark 1 of 3"`);
  });
});

describe("a page crowded with word marks (#523)", () => {
  it("draws up to 40, and over 40 shows a chip that turns them on for the page instead", async () => {
    expect(WORD_MARK_CAP).toBe(40);
    const forty = Array.from({ length: 40 }, (_, n) => word(n));
    expect(wordMarkView(forty, true, false).capped).toBe(false);
    expect(drawnWords(await render(forty, "admin"))).toHaveLength(40);

    const many = Array.from({ length: 41 }, (_, n) => word(n));
    const view = wordMarkView(many, true, false);
    expect([view.total, view.capped, view.drawn(many[0]!.region)]).toEqual([41, true, false]);
    expect(wordMarkView(many, true, true).drawn(many[0]!.region)).toBe(true); // the chip pressed
    expect(wordsChipText(41)).toBe("41 words to check on this page");
    const html = await render(many, "admin");
    expect(drawnWords(html)).toHaveLength(0);
    expect(html).toMatch(/<button type="button" class="[^"]*"[^>]*>41 words to check on this page/);
    // Switched off, there is nothing to count: no chip.
    expect(wordMarkView(many, false, false).capped).toBe(false);
  });
});

describe("the guard for stored word marks (#523)", () => {
  it("never draws a mark over 2.5 times the page's usual word height, or 6% of the page if more", () => {
    const usual = Array.from({ length: 6 }, (_, n) => word(n, 0.02));
    expect(tallLimit(usual)).toBeCloseTo(0.06); // 2.5 x 0.02 is under 6%: 6% it is
    const strip = word(9, 0.45); // a stored strip the height of the paragraph
    const tall = word(10, 0.055); // a big written word: kept
    const view = wordMarkView([...usual, strip, tall], true, false);
    expect(view.drawn(strip.region)).toBe(false);
    expect(view.drawn(tall.region)).toBe(true);
    expect(view.drawn(usual[0]!.region)).toBe(true);
    expect(view.total).toBe(8); // still counted and listed: only not drawn
  });

  it("is not raised by a page whose marks are all strips", () => {
    const strips = Array.from({ length: 5 }, (_, n) => word(n, 0.3 + n * 0.02));
    expect(tallLimit(strips)).toBeCloseTo(0.06);
    expect(strips.some((mark) => wordMarkView(strips, true, false).drawn(mark.region))).toBe(false);
  });

  it("leaves a stored tall mark off the page as drawn", async () => {
    const html = await render([word(1), word(2, 0.45)], "reviewer");
    expect(drawnWords(html)).toHaveLength(1);
    expect(drawnWords(html)[0]).toContain("&#x27;w1&#x27;");
  });
});
