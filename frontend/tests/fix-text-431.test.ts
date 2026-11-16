// Tushar's report (28 Oct 2026, #431 and #432): Save in the Text view's fix box fixes the [?]
// it names, one action per line, a fixed word's marks turn green on the page, and the review's
// list is shut at first. Seen on a Gemini read: a line whose spot no box confirmed is flagged
// itself and comes before its [?], so Fix text used to edit the whole line.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { markLabel, settled } from "@/components/review/wordReview";
import { markChecked, markName } from "@/components/viewer/readingMarks";
import { fixBox, fixesFor, withFixes, wordToFix } from "@/components/viewer/scanned";
import type { ReadingMark, WordFlag } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const PANEL = "components/viewer/ScannedTextPanel.tsx";
const VIEW = "app/(app)/documents/[id]/DocumentView.tsx";

type Box = [number, number, number, number];
const region = (box: Box) => ({ kind: "box" as const, page: 1, box });
const flag = (kind: WordFlag["kind"], as_read: string, box: Box, changes: Partial<WordFlag> = {}): WordFlag => ({
  word_key: `${kind}-${as_read}-${box[0]}`,
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
  ...changes,
});

// "For: [?] and pulses", its spot not confirmed (a line flag), and the [?] in it (a mark).
const LINE: Box = [0.076, 0.185, 0.335, 0.21];
const line = { text: "For: [?] and pulses", region: region(LINE) };
const lineFlag = flag("line", "For: [?] and pulses", LINE);
const unread = flag("mark", "[?]", [0.144, 0.185, 0.185, 0.21], { reason: "low_confidence" });
// A signature read as [?]: the line is only that word, flagged twice on one spot.
const SIGN: Box = [0.075, 0.25, 0.2, 0.28];
const signLine = flag("line", "[?]", SIGN);
const signMark = flag("mark", "[?]", SIGN);

describe("#431 Save fixes the [?] the box names", () => {
  it("works on the line's word mark before the line's own flag, then on the line", () => {
    // Changed on purpose (#614): wordToFix is given the page's lines (a word is the smallest's).
    expect(wordToFix([lineFlag, unread], line, [line])).toBe(unread);
    expect(wordToFix([lineFlag], line, [line])).toBe(lineFlag); // its words done: the line itself
    expect(wordToFix([flag("mark", "x", [0.1, 0.5, 0.2, 0.55])], line, [line])).toBeUndefined(); // another line's
    expect(fixBox(unread)).toEqual({ hint: "Type the word you see on the page in place of [?].", draft: "" });
  });

  it("shows the word typed in the line, whatever the line's own flag", () => {
    const typed = { ...unread, text: "Wheat" };
    // The line's flag as read is the whole line: it used to swallow the line and hide the fix.
    expect(withFixes(line, [{ ...lineFlag }, typed])).toBe("For: Wheat and pulses");
    // A line a person typed in full shows as typed.
    expect(withFixes(line, [{ ...lineFlag, text: "For: Rice and pulses" }, typed])).toBe("For: Rice and pulses");
  });

  it("settles a line that is only the word with the same Save, and no other line", () => {
    // Changed on purpose (#614): fixesFor is given the row the Save came from.
    const signRow = { text: "[?]", region: region(SIGN) };
    expect(fixesFor(signMark, [signLine, signMark], signRow)).toEqual([signMark, signLine]);
    expect(fixesFor(unread, [lineFlag, unread], line)).toEqual([unread]); // the line holds more than the word
    expect(fixesFor(lineFlag, [lineFlag, unread], line)).toEqual([lineFlag]);
  });

  it("saves every flag fixesFor names, one after the other, and closes only when all went through", () => {
    const panel = read(PANEL);
    // Changed on purpose (#614): with the page's lines, and the row the Save came from.
    expect(panel).toContain("const toFix = first && onFix ? wordToFix(flags, line, lines) : undefined;");
    // Changed on purpose (#443): Confirm sends each word's own text (asRead).
    expect(panel).toContain(
      "for (const each of fixesFor(flag, flags, row)) if (done.ok) done = await onFix(each, asRead ? each.text : typed);",
    );
    expect(panel).toContain("if (done.ok) closeFix(index);");
    expect(panel).toContain('const ask = toFix ? fixBox(toFix.kind === "line" ? { text: read } : toFix) : null;');
  });
});

describe("#431 one action: the open box, never a second Fix text beside it", () => {
  const panel = read(PANEL);

  it("shows the row's Fix text only while its box is shut, always the same quiet button", () => {
    expect(panel).toMatch(/\{toFix && ask && !box && \(\s*<button\s+type="button"\s+className=\{`btn btn-quiet \$\{styles\.addMeaning\}`\}\s+aria-expanded=\{false\}/);
    expect(panel).not.toContain("fixInput.current?.focus() : openFix");
    // Opening puts the keys in the field; Save, Cancel or Esc gives focus back to the line.
    expect(panel).toContain("useEffect(() => fixInput.current?.focus(), [fixKey]);");
    expect(panel).toContain('rows.current[index]?.querySelector("button")?.focus();');
    expect(panel).toMatch(/<button type="submit" className="btn btn-primary"/);
  });
});

describe("#431 a fixed or confirmed word's marks turn green on the page", () => {
  const mark = (quality: ReadingMark["quality"], box: Box): ReadingMark => ({ quality, region: region(box), text: "[?]" });

  it("names a settled word for what it is now", () => {
    expect(settled({ state: "reviewed" })).toBe(true);
    expect(settled({ state: "resolved" })).toBe(true);
    expect(settled({ state: "open" })).toBe(false);
    expect(markLabel({ ...unread, text: "Wheat", state: "reviewed" })).toBe("Reviewed: 'Wheat'");
    expect(markLabel(unread)).toBe("Flagged for review: '[?]', unclear source, please confirm, reason: spot on the page not confirmed");
  });

  it("turns a reading problem green once every flag on its spot is settled, red again once undone", () => {
    const done = { ...signMark, state: "reviewed" as const };
    expect(markChecked(mark("illegible", SIGN), [done, { ...signLine, state: "reviewed" }])).toBe(true);
    expect(markChecked(mark("illegible", SIGN), [done, signLine])).toBe(false); // one still open
    expect(markChecked(mark("illegible", SIGN), [signMark])).toBe(false); // undone: open again
    expect(markChecked(mark("unsure", [0.38, 0.09, 0.46, 0.12]), [done])).toBe(false); // nothing flagged there
    expect(markName(mark("illegible", SIGN), 0, 2, true)).toBe("Hard to read '[?]', mark 1 of 2, checked");
  });

  it("draws every flagged word on the page, a settled one green with a tick, from a paper-fixed token", () => {
    const view = read(VIEW);
    expect(view).toContain("const pageMarks = page ? review.flags.filter((flag) => flag.page_no === page.page_no) : [];");
    expect(view).toContain("flags={review.flags}"); // the PDF viewer
    expect(view).toContain("flags={pageMarks}"); // a photo's page
    expect(view).toContain("const openFlags = review.flags.filter((flag) => !settled(flag));"); // Text and review keep the open ones
    const page = css("components/viewer/PageViewer.module.css");
    expect(page).toMatch(/\.flag\[data-state="reviewed"\],\s*\.flag\[data-state="resolved"\] \{\s*border-bottom: 2px solid var\(--verified-on-paper\);/);
    expect(page).toMatch(/\.flag\[data-state="reviewed"\]::after,\s*\.flag\[data-state="resolved"\]::after \{[^}]*content: "✓";[^}]*color: var\(--verified-on-paper\);/);
    expect(page).toMatch(/\.problem\[data-state="checked"\] \{\s*border-color: var\(--verified-on-paper\);/);
    // After the band rules, so the green wins at the same specificity.
    expect(page.indexOf('.flag[data-state="reviewed"]')).toBeGreaterThan(page.indexOf('.flag[data-band="review"]::after'));
    expect(page.indexOf('.problem[data-state="checked"]')).toBeGreaterThan(page.indexOf('.problem[data-quality="illegible"]'));
    for (const viewer of ["components/viewer/PageViewer.tsx", "components/viewer/PdfDocumentViewer.tsx"]) {
      expect(read(viewer)).toContain('data-state={checked ? "checked" : undefined}');
      expect(read(viewer)).toContain("const checked = markChecked(mark, flags);");
    }
  });

  it("uses one green set once, the light --success, 3:1 or more on the white page in both themes", () => {
    const globals = read("app/globals.css");
    expect(globals.match(/--verified-on-paper: #[0-9a-f]+/g)).toEqual(["--verified-on-paper: #1b7a4b"]);
    expect(globals).toMatch(/:root \{[^}]*--success: #1b7a4b;/);
    const channel = (hex: string) => {
      const value = parseInt(hex, 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    };
    const ink = 0.2126 * channel("1b") + 0.7152 * channel("7a") + 0.0722 * channel("4b");
    expect((1 + 0.05) / (ink + 0.05)).toBeGreaterThanOrEqual(4.5); // text-grade, on --paper #ffffff
  });
});

describe("#432 the review's word list starts shut", () => {
  it("is a native disclosure with its count, shut until opened, kept while the panel stays", () => {
    const panel = read("app/(app)/documents/[id]/ReviewPanel.tsx");
    const list = panel.slice(panel.indexOf("<details className={styles.listToggle}>"), panel.indexOf("</details>"));
    expect(list).toMatch(/<summary className=\{styles\.listSummary\}>[\s\S]*Flagged words <span className="tabular">\(\{flags\.length\.toLocaleString\("en-IN"\)\}\)<\/span>\s*<\/summary>/);
    expect(list).toContain('<ul className={styles.list} aria-label="Flagged words">');
    expect(panel).not.toMatch(/<details className=\{styles\.listToggle\}[^>]*\bopen\b/); // shut at first
    const sheet = css("app/(app)/documents/[id]/WordReview.module.css");
    expect(sheet).toMatch(/\.listSummary \{[^}]*min-height: var\(--control-min\);[^}]*cursor: pointer;/);
    expect(sheet).toMatch(/\.listToggle\[open\] > \.listSummary \.listChevron \{\s*transform: rotate\(90deg\);/);
  });
});
