// #527 (Tushar, 7 Nov 2026): a reviewer or an admin corrects a line of the clean copy. The call
// (POST lines/{key}/edit; Undo is the word review's, tested in word-review.test.ts), the words,
// the text box's first text, the label and its Undo, the errors in plain words; and the panel
// itself, rendered with react-dom in a small fake DOM (tests/fake-dom.ts) and pressed: who sees
// Edit, Enter and Esc, focus back on the line, the toast, Undo, and Open wide.

import { act, createElement } from "react";
import type { Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { canReview } from "@/components/review/review";
import { NOT_SENT } from "@/components/review/wordReview";
import { Corrected, ReadClearlyPanel } from "@/components/viewer/ReadClearlyPanel";
import {
  LINE_CHANGED,
  LINE_SAVED,
  LINE_SAVED_HERE,
  LINE_UNDONE,
  MAX_LINE,
  STILL_READING,
  correctedText,
  lineFailure,
  oneLine,
  parts,
  readAsText,
  replaceLine,
} from "@/components/viewer/clearCopy";
import { ApiError, editLine } from "@/lib/api";
import type { ClearCopy, LineCorrection, LineSaved, ReadLine } from "@/lib/types";
import { FakeDocument, FakeElement, fire, install } from "./fake-dom";

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

describe("the text box's first text", () => {
  it("is the line as one line: breaks and tabs a single space, so no words are glued", () => {
    expect(oneLine("Ram Kumar\nS/o Shyam")).toBe("Ram Kumar S/o Shyam");
    expect(oneLine("Ram Kumar\r\n\r\nS/o\tShyam ")).toBe("Ram Kumar S/o Shyam");
    expect(oneLine("Ram\u2028Kumar")).toBe("Ram Kumar"); // a line separator is a line break
  });

  it("has no hidden or control character, as the server checks (names.clean_name)", () => {
    expect(oneLine("Ku\u00admar")).toBe("Kumar"); // a soft hyphen
    expect(oneLine("a\u202eb\u200bc\u0000d\u0085e")).toBe("abcde");
    expect(oneLine("GST\ud800 2026")).toBe("GST 2026"); // a lone surrogate (port of b8faa1d3, #564)
    expect(oneLine("\u0915\u094d\u200d\u0937")).toBe("\u0915\u094d\u200d\u0937"); // ZWJ stays: Devanagari needs it
    const scotland = "\u{1F3F4}\u{E0067}\u{E0062}\u{E0073}\u{E0063}\u{E0074}\u{E007F}";
    expect(oneLine(`Flag ${scotland}`)).toBe(`Flag ${scotland}`);
  });
});

// --- The panel, rendered and pressed ---

const RAW = "Ram Kumar\nS/o Shyam"; // as the reader gave it: two written lines
const NAME_KEY = "cd".repeat(32);
const named = (changes: Partial<ReadLine> = {}) => line({ key: NAME_KEY, text: RAW, ...changes });
const copyOf = (...lines: ReadLine[]): ClearCopy => ({ pages: [{ page_no: 1, lines, marks: [] }], truncated: false });
const fixedLine = () => named({ text: "Ram Kumar S/o Shyam Lal", correction: { ...correction, before_text: RAW } });
const savedReply = (pieces = 1): LineSaved => ({
  change_id: "c1",
  line: fixedLine(),
  written: { pieces, values: 0 },
  skipped: { pieces: 1 - pieces, values: 0 },
});

let doc: FakeDocument;
let root: Root | null = null;
let calls: { url: string; method: string; body: unknown }[] = [];

/** fetch answers each "METHOD /path" from `routes`, and records each call. */
function serve(routes: Record<string, unknown>) {
  calls = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const method = init.method ?? "GET";
    calls.push({ url, method, body: init.body ? JSON.parse(String(init.body)) : undefined });
    const reply = routes[`${method} ${url}`];
    if (reply === undefined) throw new Error(`no route for ${method} ${url}`);
    return new Response(JSON.stringify(reply), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

async function show(copy: ClearCopy, canEdit: boolean, routes: Record<string, unknown> = {}) {
  serve({ "GET /api/documents/d1/clear-copy": copy, ...routes });
  const container = doc.body.appendChild(doc.createElement("div")) as FakeElement;
  // Loaded once the fake DOM is in place: react-dom looks for a DOM as it loads (fake-dom.ts).
  const { createRoot } = await import("react-dom/client");
  root = createRoot(container as unknown as Element);
  const props = { documentId: "d1", active: null, onShow: () => undefined, canEdit };
  await act(async () => root!.render(createElement(ReadClearlyPanel, props)));
  return container;
}

async function unmount() {
  await act(async () => root?.unmount());
  root = null;
}

const all = (within: FakeElement) => within.all();
const labelled = (within: FakeElement, start: string) =>
  all(within).filter((element) => element.getAttribute("aria-label")?.startsWith(start));
const byLabel = (within: FakeElement, label: string) => all(within).find((element) => element.getAttribute("aria-label") === label) ?? null;
const byText = (within: FakeElement, tag: string, text: string) =>
  all(within).find((element) => element.tagName === tag && element.textContent.trim() === text) ?? null;
/** The reader's line button that reads `text`. */
const lineButton = (within: FakeElement, text: string) =>
  all(within).find((element) => element.getAttribute("aria-pressed") !== null && element.textContent === text) ?? null;
const textBox = (within: FakeElement) => all(within).find((element) => element.tagName === "INPUT") ?? null;
const press = (element: FakeElement | null) => act(async () => void fire(element!, "click", { button: 0 }));
const key = (element: FakeElement | null, name: string) => act(async () => void fire(element!, "keydown", { key: name }));
async function type(box: FakeElement | null, text: string) {
  box!.value = text;
  await act(async () => void fire(box!, "input"));
}

beforeEach(() => {
  doc = install();
});

afterEach(unmount);

describe("the panel: who sees Edit", () => {
  it("gives Edit to a reviewer and an admin, never to a member", async () => {
    for (const role of ["reviewer", "admin", "member"] as const) {
      const panel = await show(copyOf(line(), named()), canReview(role));
      const edits = labelled(panel, "Edit line").map((element) => element.getAttribute("aria-label"));
      expect([role, edits]).toEqual([role, role === "member" ? [] : ["Edit line 1", "Edit line 2"]]);
      await unmount();
    }
  });

  it("draws a member's line as before: no row around it, no key on it", async () => {
    const panel = await show(copyOf(line()), false);
    const button = lineButton(panel, "Total 1100")!;
    expect((button.parentNode as FakeElement).tagName).toBe("P");
    expect(button.getAttribute("data-line")).toBeNull();
    expect(button.getAttribute("aria-describedby")).toBeNull();
  });
});

describe("the panel: correcting a line", () => {
  const edit = `POST /api/documents/d1/lines/${NAME_KEY}/edit`;

  it("opens a labelled text box with the line as one line, focused", async () => {
    const panel = await show(copyOf(named()), true);
    await press(byLabel(panel, "Edit line 1"));
    const box = textBox(panel)!;
    expect([box.value, box.getAttribute("aria-label"), box.getAttribute("maxLength")]).toEqual([
      "Ram Kumar S/o Shyam",
      "Correct line 1",
      String(MAX_LINE),
    ]);
    expect(doc.activeElement).toBe(box);
  });

  it("saves on Enter: the text typed and the text seen go, the line reads anew, focus goes back to it", async () => {
    const panel = await show(copyOf(named()), true, { [edit]: savedReply() });
    await press(byLabel(panel, "Edit line 1"));
    await type(textBox(panel), "Ram Kumar S/o Shyam Lal ");
    await key(textBox(panel), "Enter");

    expect(calls.at(-1)).toEqual({
      url: `/api/documents/d1/lines/${NAME_KEY}/edit`,
      method: "POST",
      body: { text: "Ram Kumar S/o Shyam Lal", expected_text: RAW },
    });
    expect(textBox(panel)).toBeNull();
    expect(doc.activeElement).toBe(lineButton(panel, "Ram Kumar S/o Shyam Lal"));
    expect(panel.textContent).toContain(LINE_SAVED);
    expect(panel.textContent).toContain("Corrected by Ravi Sharma, 7 Nov 2026");
    expect(byLabel(panel, "Undo the correction to line 1")).not.toBeNull();
    expect(byText(panel, "SPAN", `Read as: ${RAW}`)).not.toBeNull();
  });

  it("says so when no search piece could take the new text", async () => {
    const panel = await show(copyOf(named()), true, { [edit]: savedReply(0) });
    await press(byLabel(panel, "Edit line 1"));
    await type(textBox(panel), "Ram Kumar S/o Shyam Lal");
    await press(byText(panel, "BUTTON", "Save"));
    expect(panel.textContent).toContain(LINE_SAVED_HERE);
    expect(panel.textContent).not.toContain(LINE_SAVED);
  });

  it("sends nothing when the text is the line as it reads", async () => {
    const panel = await show(copyOf(named()), true);
    await press(byLabel(panel, "Edit line 1"));
    await key(textBox(panel), "Enter");
    expect(calls.map((call) => call.method)).toEqual(["GET"]);
    expect(textBox(panel)).toBeNull();
    expect(doc.activeElement).toBe(lineButton(panel, RAW));
  });

  it("cancels on Esc or Cancel, sending nothing, and focus goes back to the line", async () => {
    const panel = await show(copyOf(named()), true);
    for (const close of [() => key(textBox(panel), "Escape"), () => press(byText(panel, "BUTTON", "Cancel"))]) {
      await press(byLabel(panel, "Edit line 1"));
      await type(textBox(panel), "something else");
      await close();
      expect(textBox(panel)).toBeNull();
      expect(doc.activeElement).toBe(lineButton(panel, RAW));
    }
    expect(calls.map((call) => call.method)).toEqual(["GET"]);
  });

  it("shows a refusal in plain words and keeps the box open", async () => {
    const panel = await show(copyOf(named()), true);
    const refusal = { error: { code: "changed_since", message: "x" } };
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(refusal), { status: 409, headers: { "Content-Type": "application/json" } }));
    await press(byLabel(panel, "Edit line 1"));
    await type(textBox(panel), "Ram Kumar S/o Shyam Lal");
    await key(textBox(panel), "Enter");
    expect(textBox(panel)).not.toBeNull();
    expect(all(panel).find((element) => element.getAttribute("role") === "alert")?.textContent).toBe(LINE_CHANGED);
  });
});

describe("the panel: a corrected line", () => {
  it("says who corrected it, with Undo, which puts the line back and focus on it", async () => {
    const panel = await show(copyOf(fixedLine()), true);
    expect(panel.textContent).toContain("Corrected by Ravi Sharma, 7 Nov 2026");
    serve({ "POST /api/documents/d1/changes/c1/undo": { id: "c1" }, "GET /api/documents/d1/clear-copy": copyOf(named()) });

    await press(byLabel(panel, "Undo the correction to line 1"));

    expect(calls.map((call) => `${call.method} ${call.url}`)).toEqual([
      "POST /api/documents/d1/changes/c1/undo",
      "GET /api/documents/d1/clear-copy",
    ]);
    expect(panel.textContent).toContain(LINE_UNDONE);
    expect(panel.textContent).not.toContain("Corrected by");
    expect(doc.activeElement).toBe(lineButton(panel, RAW));
  });

  it("shows a member the note without Undo", async () => {
    const panel = await show(copyOf(fixedLine()), false);
    expect(panel.textContent).toContain("Corrected by Ravi Sharma, 7 Nov 2026");
    expect(byText(panel, "BUTTON", "Undo")).toBeNull();
  });
});

describe("the panel: Open wide", () => {
  it("shows the table again without Edit, Undo or the lines' ids", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor(private readonly changed: () => void) {}
        observe(element: object) {
          Object.assign(element, { scrollWidth: 900, clientWidth: 300 }); // wider than the panel
          this.changed();
        }
        disconnect() {}
      },
    );
    const cell = (col: number, changes: Partial<ReadLine> = {}) =>
      line({ key: String(col).repeat(64), text: `cell ${col}`, kind: "table_cell", table: { id: "t1", row: 0, col, header: false }, ...changes });
    const panel = await show(copyOf(cell(1), cell(2, { correction })), true);
    expect(labelled(panel, "Edit line")).toHaveLength(2);

    await press(byText(panel, "BUTTON", "Open wide"));

    const dialog = all(panel).find((element) => element.tagName === "DIALOG")!;
    expect(dialog.textContent).toContain("cell 1");
    expect(dialog.textContent).toContain("Corrected by Ravi Sharma, 7 Nov 2026");
    expect([...labelled(dialog, "Edit line"), ...labelled(dialog, "Undo")]).toEqual([]);
    const ids = all(dialog).filter((element) => element.getAttribute("data-line") !== null || element.getAttribute("id")?.startsWith("read-as"));
    expect(ids).toEqual([]);
  });
});
