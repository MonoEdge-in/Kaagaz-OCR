// PRD 9 G1 (Round 1, CC-01 to CC-05). F-12: a fix saved in Text did not show there: the line was
// shown raw with the fixes laid over it in the browser (withFixes), and a flag's text as read (its
// whitespace squashed) was never found in a raw line holding a line break or a double space. The
// server now sends each main reader line's text with its fixes, as Clean copy does (views.clear_line,
// backend/tests/test_api_text_fixes.py). F-13 (D6): the fix box has Cancel and Save only; Save left
// as it began (spaces aside) confirms the word as read. F-14: the tab's badge, the header and the
// chips said 31, 11 and 1/32: one number now (the open words, toSettle), a loader until it is known,
// the chips the open items only, and "Everything is checked" at zero.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AttentionPanel, attentionCount } from "@/app/(app)/documents/[id]/DocumentPanels";
import { ALL_CHECKED, toSettle } from "@/components/viewer/pageProblems";
import { openMarks, pageMarkName } from "@/components/viewer/readingMarks";
import * as scanned from "@/components/viewer/scanned";
import { TWICE, fixBox, rowFlag, seenTwice, squash, wordToFix } from "@/components/viewer/scanned";
import type { PageSummary, ReadingMark, WordFlag } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const PANEL = read("components/viewer/ScannedTextPanel.tsx");
const VIEW = read("app/(app)/documents/[id]/DocumentView.tsx");

type Box = [number, number, number, number];
const region = (box: Box) => ({ kind: "box" as const, page: 1, box });
const BLOCK: Box = [0.1, 0.1, 0.9, 0.5]; // a Mistral block's box, shared by each of its lines
const flag = (kind: WordFlag["kind"], as_read: string, box: Box, more: Partial<WordFlag> = {}): WordFlag => ({
  word_key: `${kind}-${as_read}`,
  kind,
  page_no: 1,
  region: region(box),
  as_read,
  text: as_read,
  band: "review",
  reason: "spot_unconfirmed",
  reason_text: "spot on the page not confirmed",
  score: null,
  alternatives: [],
  value_id: null,
  state: "open",
  ensemble: null,
  change: null,
  ...more,
});
const page = (page_no: number, more: Partial<PageSummary> = {}): PageSummary => ({
  page_no,
  width: 595,
  height: 842,
  unit: "px",
  has_image: true,
  route: "ocr",
  effective_dpi: 300,
  low_resolution: false,
  read_by: null,
  read_quality: "uncertain",
  read_issues: ["illegible_words"],
  unsure_words: 0,
  illegible_words: 31,
  unpinned_lines: 0,
  accepted_by: null,
  accepted_at: null,
  read_again_requested_at: null,
  problems: [],
  ...more,
});

describe("PRD 9 F-12 the Text tab shows the fixes from the server", () => {
  it("draws each line's text as the server sends it, with no fix laid over it in the browser", () => {
    expect(scanned).not.toHaveProperty("withFixes");
    expect(PANEL).not.toContain("withFixes");
    expect(PANEL).toContain("const text = visibleText(line.text);");
    expect(VIEW).not.toContain("fixed={review.flags");
  });

  it("marks a corrected line as Clean copy does, with Undo for a reviewer, then asks for the lines again", () => {
    expect(PANEL).toContain("const correction = first ? (line.correction ?? null) : null;");
    expect(PANEL).toMatch(/\{correction && \(\s*<Corrected/);
    expect(PANEL).toContain("onUndo={onFix ? () => void undo(correction) : undefined}");
    expect(PANEL).toMatch(/await undoChange\(documentId, correction\.change_id\);[\s\S]*refresh\(\);/);
    expect(VIEW).toContain("onUndone={() => review.reload()}");
  });

  it("finds a line's own flag by its key once a word in it is fixed, whatever its spaces", () => {
    const own = flag("line", "Shop 4, DAREGAO West", BLOCK, { word_key: "k-road" });
    const fixed = { text: "Shop 4, Goregaon West", region: region(BLOCK), key: "k-road" };
    expect(wordToFix([own], fixed, [fixed])).toBe(own);
    const raw = { text: "Shop 4,  DAREGAO\nWest", region: region(BLOCK) }; // no key: its text, squashed
    expect(wordToFix([own], raw, [raw])).toBe(own);
  });
});

describe("PRD 9 F-13 the fix box: Cancel and Save, and Save unchanged confirms", () => {
  it("offers Cancel and Save only", () => {
    expect(PANEL).not.toMatch(/>\s*Confirm\s*</);
    expect(PANEL).toMatch(/Cancel\s*<\/button>\s*<div className=\{styles\.fixSettle\}>\s*<button type="submit"/);
  });

  it("confirms a word saved as it began, spaces aside, and saves any other text as the fix", () => {
    expect(PANEL).toContain('const asRead = draft !== "" && squash(typed) === squash(draft);');
    expect(PANEL).toContain("done = await onFix(each, asRead ? each.text : typed);");
    expect(PANEL).toContain("void saveFix(toFix, index, ask.draft);");
    expect(squash("Shop 4,  DAREGAO\nWest ")).toBe(squash("Shop 4, DAREGAO West"));
    expect(squash("नमस्ते  दुनिया")).toBe("नमस्ते दुनिया");
  });

  it("starts a line's box from its text on one line, never a line break an input would glue shut", () => {
    expect(PANEL).toContain('fixBox(toFix.kind === "line" ? { text: oneLine(line.text) } : toFix)');
  });

  it("pre-selects the flagged word alone, never a whole line", () => {
    expect(PANEL).toContain('selectWord.current = flag.kind !== "line";');
    expect(PANEL).toContain("if (selectWord.current) fixInput.current?.select();");
  });

  it("says a stray mark is left as read by saving it as it is", () => {
    expect(fixBox({ text: "." }).hint).toBe("'.' may be a stray mark. Save it as it is to leave it as read, or type what the page shows.");
  });
});

describe("PRD 9 F-14 Needs attention: one number, open items only", () => {
  it("is not known (null) until the words come, never a number from the pages", () => {
    expect(toSettle([page(1)], null)).toBeNull();
    expect(attentionCount([page(1)], null)).toBeNull();
  });

  it("counts the open words once they come, as the header and the badge both say", () => {
    const open = Array.from({ length: 11 }, () => ({ page_no: 1 }));
    expect(toSettle([page(1)], open)).toEqual({ count: 11, pages: [1] });
    expect(attentionCount([page(1)], open.slice(3))).toBe(8); // three fixed: down by three
  });

  it("gives a row the red flag of its own line only, never another line of the same block", () => {
    const fixedLine = { text: "Goregaon Road", region: region(BLOCK), key: "k-1" };
    const openLine = { text: "Mumbai  400\n104", region: region(BLOCK), key: "k-2" };
    const open = flag("line", "Mumbai 400 104", BLOCK, { word_key: "k-2" });
    expect(rowFlag([open], fixedLine)).toBeUndefined();
    expect(rowFlag([open], openLine)).toBe(open);
    expect(rowFlag([open], { text: openLine.text, region: openLine.region })).toBe(open); // by text, squashed
  });

  const mark = (text: string, box: Box, quality: ReadingMark["quality"] = "illegible"): ReadingMark => ({
    quality,
    region: region(box),
    text,
  });
  const marks = [mark("[?]", [0.1, 0.1, 0.2, 0.15]), mark("Mumbai", BLOCK, "unpinned"), mark("Road", BLOCK, "unpinned")];

  it("lists the marks still open, each keeping its place on the page", () => {
    const open = [flag("mark", "[?]", [0.1, 0.1, 0.2, 0.15]), flag("line", "Road", BLOCK)];
    expect(openMarks(marks, open)).toEqual([
      { mark: marks[0], index: 0 },
      { mark: marks[2], index: 2 },
    ]);
    expect(openMarks(marks, [])).toEqual([]);
  });

  const panel = (open: WordFlag[] | null, pages = [page(1)]) =>
    renderToStaticMarkup(
      createElement(AttentionPanel, {
        status: "ready",
        pages,
        open,
        current: 1,
        marks,
        onPage: () => undefined,
        onMark: () => undefined,
        onCheck: () => undefined,
      }),
    );

  it("shows a loader, no number and no chip, until the words come", () => {
    const html = panel(null);
    expect(html).toContain("Counting what is left to check");
    expect(html).not.toMatch(/\d+ to check/);
    expect(html).not.toContain("1/");
  });

  it("numbers the chips over the open items only", () => {
    const html = panel([flag("mark", "[?]", [0.1, 0.1, 0.2, 0.15]), flag("line", "Road", BLOCK)]);
    expect(html).toContain("2 to check");
    expect(html).toContain("1/2");
    expect(html).toContain("2/2");
    expect(html).not.toContain("3/3");
    expect(html).not.toContain(">Mumbai<");
  });

  it("says Everything is checked at zero", () => {
    expect(ALL_CHECKED).toBe("Everything is checked");
    const html = panel([], [page(1, { read_issues: ["illegible_words"], accepted_at: "2026-11-18T10:00:00Z" })]);
    expect(html).toContain(ALL_CHECKED);
    expect(html).not.toContain("to check<");
  });

  it("drops the tab's badge at zero, shows a loader in it until known, and keeps the tab while it is open", () => {
    expect(VIEW).toContain("const attention = attentionCount(detail.pages, openWords);");
    expect(VIEW).toMatch(/count: attention === null \? \(review\.failed \? undefined : null\) : attention \|\| undefined/);
    expect(VIEW).toContain('tabChoice?.tab === "attention"');
    expect(VIEW).toMatch(/item\.count === null \? \(\s*<span className="spinner/);
  });
});

// PRD 9 review of #683.
describe("PRD 9 F-13 review: an empty [?] is never confirmed, and a word twice in its line is not saved blind", () => {
  it("asks for the word when Save is pressed on an empty [?] box, never confirms it as read", () => {
    expect(fixBox({ text: "[?]" }).draft).toBe("");
    expect(PANEL).toContain('if (typed === "" && !asRead) return setFixing({ ...fixing, error: "Type the word you see on the page." });');
  });

  it("says to fix the whole line when the word is in it more than once, and saves nothing", () => {
    const twice = flag("mark", "[?]", [0.1, 0.1, 0.2, 0.15]);
    expect(seenTwice(twice, { text: "[?] Sharma  [?]" })).toBe(true);
    expect(seenTwice(twice, { text: "[?] Sharma Traders" })).toBe(false);
    expect(seenTwice(flag("line", "[?] Sharma [?]", BLOCK), { text: "[?] Sharma [?]" })).toBe(false); // the line itself
    expect(TWICE).toBe("This word appears more than once in the line. Fix the whole line in Clean copy instead.");
    expect(PANEL).toContain("if (!asRead && seenTwice(flag, row)) return setFixing({ ...fixing, error: TWICE });");
  });

  it("says Could not count in the header when the words could not be loaded, not a spinner for ever", () => {
    const html = renderToStaticMarkup(
      createElement(AttentionPanel, {
        status: "ready",
        pages: [page(1)],
        open: null,
        failed: true,
        current: 1,
        marks: [],
        onPage: () => undefined,
        onMark: () => undefined,
        onCheck: () => undefined,
      }),
    );
    expect(html).toContain("Could not count");
    expect(html).not.toContain("spinner");
    expect(VIEW).toContain("failed={review.failed}");
  });
});

// PRD 9 F-14 (owner, 19 Nov 2026): the page's marks said "mark 17 of 32" where the chips said "2 of 11".
describe("PRD 9 F-14 a mark on the page is named as its chip", () => {
  const spot = (box: Box, text: string, quality: ReadingMark["quality"] = "illegible"): ReadingMark => ({ quality, region: region(box), text });
  const marks = [spot([0.1, 0.1, 0.2, 0.15], "[?]"), spot([0.3, 0.1, 0.4, 0.15], "Rs", "unsure"), spot([0.5, 0.1, 0.6, 0.15], "40")];
  const flags = [
    flag("mark", "[?]", [0.1, 0.1, 0.2, 0.15], { state: "reviewed" }), // fixed: checked
    flag("mark", "40", [0.5, 0.1, 0.6, 0.15]), // open
  ];

  it("numbers an open mark over the open marks, and a checked or unflagged one not at all", () => {
    expect(pageMarkName(marks, 2, flags)).toBe("Hard to read '40', mark 1 of 1");
    expect(pageMarkName(marks, 0, flags)).toBe("Hard to read '[?]', checked");
    expect(pageMarkName(marks, 1, flags)).toBe("Unsure word 'Rs'"); // a check-band word: no review item
  });

  it.each(["components/viewer/PageViewer.tsx", "components/viewer/PdfDocumentViewer.tsx"])("%s names each mark so", (path) => {
    expect(read(path)).toContain("const name = pageMarkName(problems, index, flags);");
  });
});
