// #527 (Tushar, 7 Nov 2026): a reviewer or an admin corrects a line of the clean copy. The call
// (POST lines/{key}/edit, Undo through the word review's undo), the words, the label and its
// Undo, the errors in plain words, Enter and Esc, focus back on the line, and who sees Edit.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  LINE_CHANGED,
  LINE_SAVED,
  LINE_UNDONE,
  MAX_LINE,
  STILL_READING,
  correctedText,
  lineFailure,
  parts,
  readAsText,
  replaceLine,
} from "@/components/viewer/clearCopy";
import { Corrected } from "@/components/viewer/ReadClearlyPanel";
import { NOT_SENT } from "@/components/review/wordReview";
import { ApiError, editLine, undoChange } from "@/lib/api";
import type { ClearCopy, LineCorrection, ReadLine } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

const KEY = "ab".repeat(32);
const correction: LineCorrection = { before_text: "Total 1180", by: "Ravi Sharma", at: "2026-11-07T05:00:00Z", change_id: "c1" };
const line = (changes: Partial<ReadLine> = {}): ReadLine => ({
  text: "Total 1100",
  confidence: null,
  origin: "ocr",
  kind: "paragraph",
  region: { kind: "box", page: 1, box: [0.1, 0.1, 0.9, 0.14] },
  key: KEY,
  correction: null,
  ...changes,
});

/** fetch answers `reply` (or an error) and records each call. */
function answer(reply: unknown, httpStatus = 200) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(reply), { status: httpStatus, headers: { "Content-Type": "application/json" } });
  });
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("the calls", () => {
  it("saves a line: POST lines/{key}/edit with the new text and the text the person saw", async () => {
    const saved = { change_id: "c1", line: line({ text: "Total 1180", correction }) };
    const calls = answer(saved);
    expect(await editLine("d1", KEY, "Total 1180", "Total 1100")).toEqual(saved);
    expect(calls).toEqual([
      { url: `/api/documents/d1/lines/${KEY}/edit`, method: "POST", body: { text: "Total 1180", expected_text: "Total 1100" } },
    ]);
  });

  it("undoes through the word review's own undo", async () => {
    const calls = answer({ id: "c1" });
    await undoChange("d1", "c1");
    expect(calls).toEqual([{ url: "/api/documents/d1/changes/c1/undo", method: "POST", body: undefined }]);
    expect(read("components/viewer/ReadClearlyPanel.tsx")).toMatch(/undoChange\(documentId, correction\.change_id\)/);
  });

  it("puts the saved line in place of the old one, by its key, the rest as they were", () => {
    const other = line({ key: "cd".repeat(32), text: "Name: Sita" });
    const copy: ClearCopy = { pages: [{ page_no: 1, lines: [line(), other], marks: [] }], truncated: false };
    const saved = line({ text: "Total 1180", correction });
    const out = replaceLine(copy, saved);
    expect(out.pages[0]?.lines).toEqual([saved, other]);
    expect(out.pages[0]?.lines[1]).toBe(other);
    expect(copy.pages[0]?.lines[0]?.text).toBe("Total 1100"); // the copy itself is not changed
  });
});

describe("the words", () => {
  it("says what happened in the spec's words", () => {
    expect(LINE_SAVED).toBe("Line corrected. Search and answers use the new text.");
    expect(LINE_CHANGED).toBe("This line changed since you opened it. Reload and try again.");
    expect(STILL_READING).toBe("The document is still being read.");
    expect(LINE_UNDONE).toBe("Undone. The line reads as before.");
    expect(correctedText(correction)).toBe("Corrected by Ravi Sharma, 7 Nov 2026");
    expect(readAsText(correction)).toBe("Read as: Total 1180");
    expect(readAsText({ ...correction, before_text: "a‮b" })).toBe("Read as: a⟦U+202E⟧b"); // hidden characters shown
  });

  it("turns each refusal into plain words, the rest as the word edit does", () => {
    expect(lineFailure(new ApiError(409, "changed_since", "x"))).toBe(LINE_CHANGED);
    expect(lineFailure(new ApiError(409, "not_ready", "x"))).toBe(STILL_READING);
    expect(lineFailure(new ApiError(409, "in_trash", "This document is in the trash."))).toBe("This document is in the trash.");
    expect(lineFailure(new TypeError("Failed to fetch"))).toBe(NOT_SENT);
    expect(MAX_LINE).toBe(5000); // the API's MAX_TEXT
  });

  it("no longer marks a corrected line as not read for sure: a person set its words", () => {
    expect(parts(line({ confidence: 0.4, text: "[?] paid", correction }), [])).toEqual([{ text: "[?] paid", unsure: false }]);
    expect(parts(line({ confidence: 0.4 }), [])).toEqual([{ text: "Total 1100", unsure: true }]);
  });
});

describe("the label and Undo", () => {
  const render = (onUndo?: () => void) =>
    renderToStaticMarkup(createElement(Corrected, { correction, id: "read-as-1", n: 3, busy: false, onUndo }));

  it("says who corrected it and when, with Undo for a reviewer, and what it was read as", () => {
    const html = render(() => undefined);
    expect(html).toContain("Corrected by Ravi Sharma, 7 Nov 2026");
    expect(html).toMatch(/<button type="button" class="btn btn-quiet [^"]*" aria-label="Undo the correction to line 3"[^>]*>Undo<\/button>/);
    expect(html).toContain('id="read-as-1"');
    expect(html).toContain("Read as: Total 1180");
  });

  it("shows a member the label without Undo", () => {
    const html = render();
    expect(html).toContain("Corrected by Ravi Sharma, 7 Nov 2026");
    expect(html).not.toContain("Undo");
  });
});

describe("the screen", () => {
  const panel = read("components/viewer/ReadClearlyPanel.tsx");
  const css = read("components/viewer/ReadClearly.module.css");

  it("gives Edit to a reviewer or an admin only, on a document not in the trash", () => {
    expect(read("app/(app)/documents/[id]/DocumentView.tsx")).toMatch(
      /<ReadClearlyPanel[\s\S]{0,200}canEdit=\{canReview\(me\.user\.role\) && detail\.deleted_at === null\}/,
    );
    expect(panel).toMatch(/const lineKey = tools && canEdit \? \(each\.key \?\? null\) : null;/);
    expect(panel).toMatch(/\{lineKey !== null && \(\s*<button[\s\S]{0,120}aria-label=\{`Edit line \$\{n\}`\}/);
    expect(panel).toMatch(/onUndo=\{lineKey !== null \?/); // a member sees the label, never Undo
  });

  it("shows Edit on hover and focus only, and always where there is no hover", () => {
    expect(css).toMatch(/\.row \.edit \{[^}]*opacity: 0;/);
    expect(css).toMatch(/\.row:hover \.edit,\s*\.row:focus-within \.edit \{[^}]*opacity: 1;/);
    expect(css).toMatch(/@media \(hover: none\) \{\s*\.row \.edit \{\s*opacity: 1;/);
  });

  it("opens a labelled text box: Enter saves, Esc cancels, never mid-word in an Indian keyboard", () => {
    expect(panel).toMatch(/aria-label=\{`Correct line \$\{n\}`\}/);
    expect(panel).toMatch(/event\.key === "Enter" && !event\.nativeEvent\.isComposing/);
    expect(panel).toMatch(/event\.key === "Escape"/);
    expect(panel).toMatch(/maxLength=\{MAX_LINE\}/);
    expect(panel).toMatch(/className="btn btn-primary"[\s\S]{0,200}>\s*Save\s*</);
    expect(panel).toMatch(/className="btn"[\s\S]{0,120}>\s*Cancel\s*</);
    expect(panel).toMatch(/className="error-text" role="alert"/);
  });

  it("sends the trimmed text and the text the person saw, and toasts the spec's words", () => {
    expect(panel).toMatch(/const text = draft\.trim\(\);\s*if \(text === each\.text\.trim\(\)\) return close\(line\);/); // unchanged: nothing sent
    expect(panel).toMatch(/editLine\(documentId, line, text, each\.text\)/);
    expect(panel).toMatch(/setToast\(LINE_SAVED\)/);
    expect(panel).toMatch(/setToast\(LINE_UNDONE\)/);
    expect(panel).toMatch(/lineFailure\(caught\)/);
  });

  it("returns focus to the line after Save, Cancel and Undo", () => {
    // Save and Cancel (its button and Esc) close through close(), which marks the line.
    expect(panel).toMatch(/const close = \(line: string\) => \{\s*focusLine\.current = line;/);
    expect(panel).toMatch(/setToast\(LINE_SAVED\);\s*close\(line\);/);
    expect(panel).toMatch(/onClick=\{\(\) => close\(at\)\}>\s*Cancel/);
    expect(panel).toMatch(/event\.key === "Escape" && !busy\) \{\s*event\.preventDefault\(\);\s*event\.stopPropagation\(\);[^\n]*\s*close\(at\);/);
    expect(panel).toMatch(/await undoChange\(documentId, correction\.change_id\);\s*focusLine\.current = line;/);
    expect(panel).toMatch(/querySelector<HTMLElement>\(`\[data-line="\$\{line\}"\]`\)\?\.focus\(\)/);
    expect(panel).toMatch(/data-line=\{tools \? each\.key : undefined\}/); // the reader's line, not the printed copy's
  });

  it("prints and copies the text alone, without Edit or Undo", () => {
    expect(panel).toContain("createPortal(<div className={`print-copy ${styles.copy}`}>{copyOf(false)}</div>, document.body)");
  });

  it("keeps the label small and secondary, in theme colours", () => {
    expect(css).toMatch(/\.corrected \{[^}]*color: var\(--text-secondary\);[^}]*font-size: var\(--fs-sm\);/);
  });
});
