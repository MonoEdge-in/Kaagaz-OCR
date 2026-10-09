// Problems drawn on the page (PRD 5 U3): three marks, each a button with its own
// pattern and words, a legend, and "Show reading problems" on at first for an
// uncertain or unreadable page (components/viewer/readingMarks.ts).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MARK_WORDS, MIN_HIT_PX, legend, markName, markRect, marksOnPage, showByDefault } from "@/components/viewer/readingMarks";
import type { ReadingMark } from "@/lib/types";

const mark = (quality: ReadingMark["quality"], text = "xyz", page = 1): ReadingMark => ({
  quality,
  region: { kind: "box", page, box: [0.1, 0.1, 0.12, 0.11] },
  text,
});

describe("the marks on a page", () => {
  it("draws only box marks on this page, in the server's order", () => {
    const cell: ReadingMark = { quality: "unsure", region: { kind: "cell", page: 1, row: 1, column: 1 }, text: "a" };
    const marks = [mark("unsure"), mark("illegible", "", 2), cell, mark("unpinned")];
    expect(marksOnPage(marks, 1)).toEqual([marks[0], marks[3]]);
    expect(marksOnPage(undefined, 1)).toEqual([]);
  });

  it("names each mark for what it marks, as read, so a screen reader can tell them apart", () => {
    expect(markName(mark("unsure", "Shinde"), 0, 3)).toBe("Unsure word 'Shinde', mark 1 of 3");
    expect(markName(mark("illegible", "[?]"), 1, 3)).toBe("Couldn't read '[?]', mark 2 of 3");
    expect(markName(mark("unpinned", "  "), 2, 3)).toBe("Spot not confirmed, mark 3 of 3");
    expect(markName(mark("unsure", "a\u202eb"), 0, 1)).toBe("Unsure word 'a⟦U+202E⟧b', mark 1 of 1");
  });

  it("explains each pattern in words, only for the kinds the page has", () => {
    expect(legend([mark("unpinned"), mark("unsure"), mark("unsure")])).toEqual(["unsure", "unpinned"]);
    expect(legend([])).toEqual([]);
    expect(MARK_WORDS.unsure.legend).toBe("Unsure word (dashed)");
    expect(MARK_WORDS.illegible.legend).toBe("Couldn't read (hatched)");
    expect(MARK_WORDS.unpinned.legend).toBe("Spot not confirmed (dotted)");
  });

  it("shows reading problems at first on an uncertain or unreadable page only", () => {
    expect(showByDefault({ read_quality: "uncertain" })).toBe(true);
    expect(showByDefault({ read_quality: "unreadable" })).toBe(true);
    expect(showByDefault({ read_quality: "good" })).toBe(false);
  });

  it("places a mark like a my-words marker: never tiny, kept on the page", () => {
    const rect = markRect(mark("unsure"), 1000, 1400);
    expect(rect).not.toBeNull();
    expect(rect!.width).toBeGreaterThanOrEqual(12);
    expect(rect!.height).toBeGreaterThanOrEqual(12);
    expect(markRect({ quality: "unsure", region: { kind: "cell", page: 1, row: 1, column: 1 }, text: "" }, 10, 10)).toBeNull();
  });
});

describe("how the marks look", () => {
  const css = readFileSync(join(process.cwd(), "components/viewer/PageViewer.module.css"), "utf8");
  const globals = readFileSync(join(process.cwd(), "app/globals.css"), "utf8");

  it("gives each kind its own pattern: dashed, hatched, dotted", () => {
    expect(css).toMatch(/\.problem\[data-quality="unsure"\][^}]*border: 2px dashed/);
    expect(css).toMatch(/\.problem\[data-quality="illegible"\][^}]*repeating-linear-gradient/);
    expect(css).toMatch(/\.problem\[data-quality="unpinned"\][^}]*border: 2px dotted/);
  });

  it("gives every mark a hit area of at least 24 x 24 px", () => {
    expect(MIN_HIT_PX).toBe(24);
    expect(css).toMatch(/\.problem::before \{[^}]*top: min\(0px, calc\(50% - 12px\)\)/);
  });

  it("rings a focused mark in paper-fixed ink on a paper halo: 3:1 or more on white, in both themes", () => {
    const focus = css.match(/\.problem:focus-visible \{([^}]*)\}/)?.[1] ?? "";
    expect(focus).toMatch(/outline: 2px solid var\(--mine-on-paper\);/);
    expect(focus).toMatch(/box-shadow: 0 0 0 4px var\(--paper\);/); // fills the offset: the ring sits on paper
    // After .problem and .problem:hover at no lower specificity, so their box-shadow never replaces it.
    expect(css.indexOf(".problem:focus-visible {")).toBeGreaterThan(css.indexOf(".problem:hover {"));
    // Both tokens are set once, so the dark theme keeps them: pages are paper white in both.
    expect(globals.match(/--mine-on-paper: #[0-9a-f]+/g)).toEqual(["--mine-on-paper: #233f8c"]);
    expect(globals.match(/--paper: #[0-9a-f]+/g)).toEqual(["--paper: #ffffff"]);
    const channel = (hex: string) => {
      const value = parseInt(hex, 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    };
    const ink = 0.2126 * channel("23") + 0.7152 * channel("3f") + 0.0722 * channel("8c");
    expect((1 + 0.05) / (ink + 0.05)).toBeGreaterThanOrEqual(3); // WCAG 1.4.11 against white
  });

  it("uses one paper-fixed red, the light theme's #9e2219, the same in dark (V8)", () => {
    const tokens = globals.match(/--illegible-on-paper: (#[0-9a-f]+)/g) ?? [];
    expect(tokens).toEqual(["--illegible-on-paper: #9e2219"]);
    expect(globals).toMatch(/--danger-fg: #9e2219/);
  });
});

describe("the marks layer (PageViewer)", () => {
  it("draws each mark as a named button, with the toggle and the legend", async () => {
    const { PageViewer } = await import("@/components/viewer/PageViewer");
    const page = {
      page_no: 1,
      width: 595,
      height: 842,
      unit: "px" as const,
      has_image: true,
      route: "ocr" as const,
      effective_dpi: 300,
      low_resolution: false,
      read_by: "google/gemini-3-flash",
      read_quality: "uncertain" as const,
      read_issues: ["handwriting_hard" as const],
      unsure_words: 1,
      illegible_words: 0,
      unpinned_lines: 0,
      accepted_by: null,
      accepted_at: null,
      read_again_requested_at: null,
    };
    const html = renderToStaticMarkup(
      createElement(PageViewer, {
        document: { id: "d1", display_name: "Register.jpg", detected_type: "jpeg" },
        page,
        highlights: [],
        readingMarks: [mark("unsure", "Shinde")],
      }),
    );
    expect(html).toContain("Show reading problems");
    expect(html).toContain('type="checkbox" checked=""');
    expect(html).toContain("Unsure word (dashed)");
  });
});
