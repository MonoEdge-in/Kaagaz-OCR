// Scan states (Tushar, 16 Oct 2026; scan-review UX rules sections 3, 6 and 8): a document
// being uploaded, scanned and read keeps its place and its height, says calm, specific
// words ("Scanning page 6 of 14", "About 1 min left", "Scan complete · 12 flagged for
// review"), and its progress is also in the status bar, never a modal.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { statusBarText } from "@/components/documents/batchTray";
import { StatusCell } from "@/components/documents/DocumentsTable";
import { mergeList, rowsInPlace } from "@/components/documents/listMerge";
import { flaggedCount, flaggedText, scanPhrase, scanWords, toReview, uploadStateText } from "@/components/documents/logic";
import type { UploadState } from "@/components/documents/logic";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import { describe as describeTime, hold } from "@/lib/timeLeft";
import { markReceived } from "@/lib/timeLeft";
import type { BatchView, DocumentProgress, DocumentSummary, TimeLeft } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (file: string) => readFileSync(join(ROOT, file), "utf8");
const BATCH = "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e";

const doc = (id: string, more: Partial<DocumentSummary> = {}): DocumentSummary => ({
  id,
  display_name: `${id}.pdf`,
  detected_type: "pdf",
  size_bytes: 1000,
  status: "ready",
  failure_code: null,
  status_message: "Ready. You can ask about it.",
  page_count: 14,
  created_at: "2026-10-16T10:00:00Z",
  uploaded_by_name: null,
  folder_id: null,
  tags: [],
  deleted_at: null,
  version_no: 1,
  family_id: id,
  can_delete: true,
  attempts: 1,
  max_attempts: 5,
  newer_version: null,
  progress: null,
  time_left: null,
  doc_type: null,
  languages: [],
  pages_unreadable: 0,
  pages_uncertain: 0,
  values_to_review: 0,
  ...more,
});
const scanning = (id: string, progress: DocumentProgress, more: Partial<DocumentSummary> = {}) =>
  doc(id, { status: "clean", status_message: "Reading page 1 of 1…", progress, ...more });
const left = (seconds: number, more: Partial<TimeLeft> = {}): TimeLeft => ({
  seconds,
  since_move_seconds: 0,
  slow_after_seconds: 1000,
  items_left: 1,
  item_seconds: 30,
  after_seconds: 0,
  confidence: "low",
  elapsed_seconds: 30,
  done_fraction: 0.3,
  ...more,
});
const entry = (key: string, state: UploadState, more: Partial<UploadEntry> = {}): UploadEntry => ({
  key,
  name: `${key}.pdf`,
  size: 1000,
  batchId: BATCH,
  state,
  ...more,
});
const reading = (document: DocumentSummary): UploadState => ({ kind: "reading", documentId: document.id, document });
const done = (document: DocumentSummary): UploadState => ({ kind: "done", documentId: document.id, document });

// Section 6: never these, in any words written for these states.
const BANNED =
  /rough guess|can't read|couldn't read|sorry|oops|unfortunately|we think|maybe|probably|not sure|might be wrong|struggling|hopefully|uh-oh|hmm|perfect|flawless|guaranteed|instant|effortless|magic|!/i;

describe("the words for each state (wording table)", () => {
  it("says each step of a document being read as a chip word and the words after it", () => {
    const table: [DocumentProgress, string, string][] = [
      [{ step: "scanning", done: 0, total: 0 }, "Checking", "for viruses"],
      [{ step: "preparing", done: 2, total: 14 }, "Preparing", "page 3 of 14"],
      [{ step: "reading", done: 5, total: 14 }, "Scanning", "page 6 of 14"],
      [{ step: "reading", done: 14, total: 14 }, "Scanning", "page 14 of 14"], // never past the last
      [{ step: "reading", done: 0, total: 0 }, "Scanning", "the whole file"], // a sheet
      [{ step: "rereading", done: 0, total: 3 }, "Scanning", "3 pages again, carefully"],
      [{ step: "rereading", done: 0, total: 1 }, "Scanning", "1 page again, carefully"],
      [{ step: "understanding", done: 0, total: 14 }, "Understanding", "the document"],
      [{ step: "indexing", done: 0, total: 14 }, "Indexing", "for search"],
    ];
    for (const [progress, chip, detail] of table) {
      expect(scanWords({ progress, attempts: 1, max_attempts: 5 })).toEqual({ chip, detail });
    }
    expect(scanWords({ progress: { step: "reading", done: 5, total: 14 }, attempts: 2, max_attempts: 5 })).toEqual({
      chip: "Scanning",
      detail: "page 6 of 14 · Try 2 of 5",
    });
    expect(scanWords({ progress: null, attempts: 0, max_attempts: 5 })).toBeNull();
  });

  it("puts them in one line, and keeps the server's words while it is queued", () => {
    expect(scanPhrase(scanning("a", { step: "reading", done: 5, total: 14 }))).toBe("Scanning page 6 of 14");
    expect(scanPhrase(doc("q", { status: "received", status_message: "Waiting to start." }))).toBe("Waiting to start.");
  });

  // flagged = words_unsure + values_to_review; pages to check are said on their own.
  // Changed on purpose (plain words, 25 Oct 2026): "to check", never "flagged".
  it("says what a finished scan left to check, or Nothing to check", () => {
    expect(flaggedText(doc("n"))).toBe("Nothing to check");
    const flagged = doc("f", { words_unsure: 9, values_to_review: 3 });
    expect([flaggedCount(flagged), flaggedText(flagged), toReview(flagged)]).toEqual([12, "12 words to check", true]);
    expect(flaggedText(doc("p", { pages_unreadable: 2 }))).toBe("2 pages to check");
    expect(flaggedText(doc("b", { values_to_review: 1, pages_unreadable: 1 }))).toBe("1 word to check · 1 page to check");
    expect(flaggedText(doc("u", { words_unsure: 4, pages_uncertain: 2 }))).toBe("4 words to check"); // as attentionWords
    expect(toReview(doc("n"))).toBe(false);
  });

  it("names the upload stages Uploading, Saving and Scanning", () => {
    expect(uploadStateText({ kind: "sending", fraction: 0.2 })).toBe("Uploading");
    expect(uploadStateText({ kind: "saving" })).toBe("Saving");
    expect(uploadStateText(reading(scanning("a", { step: "reading", done: 0, total: 1 })))).toBe("Scanning");
  });

  // U7 (QA 17 Oct): the tray said "Scanning" while the reader had not started.
  it("says Waiting for a document the reader has not started, and counts it on its own", () => {
    const queued = doc("q", { status: "clean", status_message: "Reading is paused for now. It will continue on its own.", progress: null });
    expect(uploadStateText(reading(queued))).toBe("Waiting");
    const two = [entry("q", reading(queued)), entry("w", reading({ ...queued, id: "w" }))];
    expect(statusBarText(two, {}, 0)).toBe("2 documents waiting to be read");
    const a = scanning("a", { step: "reading", done: 2, total: 4 });
    expect(statusBarText([entry("a", reading(a)), two[0]!], {}, 0)).toBe(
      "Scanning 1 document · page 3 of 4 · 1 document waiting to be read",
    );
  });

  // CodeRabbit (17 Oct): a "waiting" step (held for the reader) is waiting, never "Scanning page 1".
  it("treats a document held at the waiting step as waiting, in the row and the status bar", () => {
    const held = scanning("h", { step: "waiting", done: 0, total: 3 });
    expect(uploadStateText(reading(held))).toBe("Waiting");
    expect(statusBarText([entry("h", reading(held))], {}, 0)).toBe("1 document waiting to be read");
  });

  it("says every estimate with About, Taking longer only with the time beside it", () => {
    const low = hold(null, left(65), "understanding", 0);
    expect(describeTime(low, 0, false).text).toBe("About 1 min left");
    const stuck = hold(null, left(150, { since_move_seconds: 50, slow_after_seconds: 20 }), "understanding", 0);
    expect(describeTime(stuck, 0, false).text).toBe("Taking longer than usual · About 3 min left");
  });

  it("never says (rough guess) or a banned word in the source of these states", () => {
    const files = [
      "lib/timeLeft.ts",
      "components/documents/TimeLeft.tsx",
      "components/documents/DocumentsTable.tsx",
      "components/documents/batchTray.ts",
      "components/documents/uploadStages.ts",
    ];
    for (const file of files) expect(read(file), file).not.toMatch(/\(rough guess\)|Ready in about/);
    const words = [
      ...["scanning", "preparing", "reading", "rereading", "understanding", "indexing"].map((step) =>
        scanPhrase(scanning("a", { step: step as DocumentProgress["step"], done: 1, total: 4 })),
      ),
      flaggedText(doc("n")),
      flaggedText(doc("f", { words_unsure: 2, pages_unreadable: 1 })),
      describeTime(hold(null, left(30), "understanding", 0), 0, false).text,
    ];
    for (const text of words) expect(text, text).not.toMatch(BANNED);
  });
});

describe("one calm, fixed-height status cell", () => {
  const html = (document: DocumentSummary, inTrash = false) =>
    renderToStaticMarkup(createElement(StatusCell, { document, inTrash }));
  const lines = (markup: string) => markup.match(/class="[^"]*statusLine[^"]*"/g)?.length ?? 0;

  it("is two lines while queued, being read, and read, so a scan finishing reflows nothing", () => {
    const busy = scanning("a", { step: "reading", done: 5, total: 14 }, { time_left: left(90) });
    markReceived([busy], performance.now());
    const queued = doc("q", { status: "received", status_message: "Waiting to start." });
    const read = doc("r", { words_unsure: 9, values_to_review: 3 });
    for (const document of [busy, queued, read, doc("n")]) expect(lines(html(document)), document.id).toBe(2);
    const scanningCell = html(busy);
    expect(scanningCell).toMatch(/data-tone="working"[^>]*>Scanning<\/span>/);
    expect(scanningCell).toContain("page 6 of 14");
    expect(scanningCell).toContain("About 2 min left");
    expect(scanningCell).toMatch(/<progress[^>]*aria-label="Progress: a.pdf"/);
    expect(scanningCell).not.toMatch(/%|rough guess|Reading page/);
    const readCell = html(read);
    expect(readCell).toMatch(/data-tone="ready"[^>]*>Scan complete<\/span>/);
    // Changed on purpose (Tushar, 16 Oct 2026, Documents table sans): "12 flagged" in full, then a
    // short "Review" link read out with what is to review and where; the UI's sans, no mono.
    expect(readCell).toContain(">12 to check</span>"); // plain words, 25 Oct 2026
    expect(readCell).not.toContain("mono");
    expect(readCell).toMatch(/<a [^>]*href="\/documents\/r"[^>]*>Review<span class="visually-hidden"> 12 words to check in r.pdf<\/span><\/a>/);
    // Changed on purpose (UI audit, 16 Oct 2026): D4, a clean read is its badge alone; the second
    // line stays laid out (two lines above), so the row keeps its height.
    expect(html(doc("n"))).not.toContain("Nothing to check");
    expect(html(doc("n"))).not.toContain(">Review<");
    expect(html(read, true)).not.toContain(">Review<"); // nothing to open for review in Trash
    expect(html(queued)).toMatch(/>Queued<\/span>.*Waiting to start\./);
    // QA U10 (4 Nov 2026): a file held (for the reader, or by a reading pause) says Waiting, not Queued.
    const held = scanning("h", { step: "waiting", done: 0, total: 3 }, { status: "received", status_message: "Reading is paused." });
    expect(html(held)).toMatch(/>Waiting<\/span>.*Reading is paused\./);
    expect(html(held)).not.toContain(">Queued<");
    expect(lines(html(held))).toBe(2);
  });

  // Changed on purpose (Tushar, 17 Oct 2026): the words wrapped and made a Rejected or Failed row
  // taller than the rest; now one line like "7 flagged", cut with "…", the whole sentence in its title.
  it("keeps the server's words on one line, whole in the title, for a failed document", () => {
    const words = "None of this file could be read. Upload a sharper scan.";
    const failed = doc("f", { status: "failed", status_message: words });
    expect(html(failed)).toContain(`title="${words}">${words}</span>`);
    expect(html(failed)).not.toContain("statusMessage");
    expect(html(failed)).toContain(">Failed</span>");
  });

  it("offers Retry on a failed row, or one still being read 6 hours on (issue 407), to whoever may read it again", () => {
    const withRetry = (document: DocumentSummary, inTrash = false) =>
      renderToStaticMarkup(createElement(StatusCell, { document, inTrash, onRetry: () => {} }));
    const stopped = "Reading is stopped until the Kaagaz team fixes the reading service. It starts again after that.";
    const stuck = doc("s", { status: "clean", stuck: true, status_message: stopped });
    const failed = doc("f", { status: "failed", status_message: "This file needs another read." });
    const button = /<button type="button" class="[^"]*statusAction[^"]*">Retry<span class="visually-hidden"> s\.pdf<\/span><\/button>/;
    expect(withRetry(stuck)).toMatch(button);
    expect(withRetry(stuck)).toContain(`title="${stopped}">${stopped}</span>`);
    expect(lines(withRetry(stuck))).toBe(2); // still two lines: the row keeps its height
    expect(withRetry(failed)).toContain(">Retry<span");
    // Not stuck yet, ready, or in Trash: no Retry. No onRetry (a member): none either.
    for (const document of [doc("q", { status: "clean", stuck: false }), doc("r")]) {
      expect(withRetry(document), document.id).not.toContain(">Retry<");
    }
    expect(withRetry(failed, true)).not.toContain(">Retry<");
    expect(html(stuck)).not.toContain(">Retry<");
  });

  it("gives each line one fixed height, the text cut to one line, the bar one width", () => {
    const css = read("components/documents/Documents.module.css");
    // Changed on purpose (Vrushit UAT, 25 Oct 2026): a row's lines after the first sit close, --row-sub.
    expect(css).toMatch(/\.statusLine \{[^}]*height: var\(--row-sub\);/);
    expect(css).toMatch(/@media \(pointer: coarse\) \{\s*\.statusLine \+ \.statusLine \{\s*height: var\(--h-lg\);/);
    expect(css).toMatch(/\.statusText \{[^}]*-webkit-line-clamp: 1;[^}]*overflow: hidden;/);
    expect(css).toMatch(/\.scanMeter \{\s*flex: none;\s*width: 4rem;/);
    // Blue is punctuation: the bar's fill is ink.
    expect(css).toMatch(/\.readMeter::-webkit-progress-value \{\s*background: var\(--text-secondary\);/);
  });
});

describe("the status bar", () => {
  const timed = (seconds: number, more: Partial<TimeLeft> = {}) => left(seconds, { confidence: "high", ...more });

  it("says uploading, scanning with pages and time, then what was flagged", () => {
    expect(statusBarText([], {}, 0)).toBe("");
    const sending = entry("invoice_march", { kind: "sending", fraction: 0.3 }, { pages: 14 });
    expect(statusBarText([sending], {}, 0)).toBe("Uploading invoice_march.pdf · 14 pages");
    expect(statusBarText([sending, entry("b", { kind: "waiting" })], {}, 0)).toBe("Uploading 2 files");

    const a = scanning("a", { step: "reading", done: 2, total: 4 }, { time_left: timed(50) });
    const b = scanning("b", { step: "understanding", done: 10, total: 10 }, { time_left: timed(10) });
    const view: Pick<BatchView, "time_left" | "documents"> = { time_left: timed(50), documents: [a, b] };
    markReceived([a, b, view], 0);
    const both = [entry("a", reading(a)), entry("b", reading(b))];
    expect(statusBarText(both, { [BATCH]: view }, 0)).toBe("Scanning 2 documents · page 13 of 14 · About 1 min left");
    expect(statusBarText([entry("a", reading(a))], {}, 0)).toBe("Scanning 1 document · page 3 of 4");

    const stuck = scanning("s", { step: "reading", done: 0, total: 6 }, { time_left: timed(130, { since_move_seconds: 2000, slow_after_seconds: 20 }) });
    markReceived([stuck], 0);
    const slow = { time_left: stuck.time_left, documents: [stuck] };
    expect(statusBarText([entry("s", reading(stuck))], { [BATCH]: slow }, 0)).toBe(
      "Scanning 1 document · page 1 of 6 · Taking longer than usual · About 3 min left",
    );

    const finished = [
      entry("r", done(doc("r", { words_unsure: 9, values_to_review: 3 }))),
      entry("n", done(doc("n"))),
      entry("x", { kind: "error", message: "Too big." }),
    ];
    expect(statusBarText(finished, {}, 0)).toBe("Scan complete · 2 documents · 12 words to check · 1 not uploaded"); // plain words, 25 Oct 2026
    expect(statusBarText([entry("n", done(doc("n")))], {}, 0)).toBe("Scan complete · 1 document · Nothing to check");
    expect(statusBarText([entry("x", { kind: "error", message: "No." })], {}, 0)).toBe("Uploads finished · 1 not uploaded");
    for (const text of [statusBarText(finished, {}, 0), statusBarText(both, { [BATCH]: view }, 0)]) {
      expect(text).not.toMatch(BANNED);
    }
  });

  it("speaks politely from the shell's status bar, with the tray in a panel there, never a modal", () => {
    const source = read("components/documents/UploadArea.tsx");
    const status = source.slice(source.indexOf("export function UploadStatus"));
    expect(status).toMatch(/<InStatusBar>/);
    expect(status).toMatch(/aria-live="polite"/);
    expect(status).toMatch(/aria-expanded=\{open\}/);
    expect(status).not.toMatch(/<dialog|showModal|role="dialog"/);
    const view = read("components/documents/DocumentsView.tsx");
    expect(view).not.toMatch(/<BatchTray/); // no tray in the flow above the list
    // Changed on purpose (UI rebuild, 21 Oct 2026, D-12): the one tray for every page is the shell's.
    expect(view).not.toMatch(/<UploadStatus/);
    expect(view).toContain("useSharedUploads(");
    expect(read("components/shell/Uploads.tsx")).toMatch(/<UploadStatus/);
    expect(read("components/shell/AppShell.tsx")).toContain("<UploadsProvider>{children}</UploadsProvider>");
    const css = read("components/documents/Documents.module.css");
    expect(css).toMatch(/\.trayPanel \{[^}]*position: fixed;[^}]*overscroll-behavior: contain;/);
  });
});

describe("a row's place", () => {
  const list = (...items: DocumentSummary[]) => ({ items, total: items.length, limit: 25, offset: 0 });

  it("stays where it is as its state changes (the list is newest first)", () => {
    const before = list(doc("c"), scanning("b", { step: "reading", done: 1, total: 4 }), doc("a"));
    const after = list(doc("c"), doc("b", { words_unsure: 2 }), doc("a"));
    const merged = mergeList(before, after);
    expect(merged.items.map((item) => item.id)).toEqual(["c", "b", "a"]);
    expect(merged.items[0]).toBe(before.items[0]); // unchanged rows keep their objects
    expect(merged.items[1]?.status).toBe("ready");
  });

  it("holds a new row while the pointer is on the list, and shows it once the pointer leaves", () => {
    const shown = [doc("b"), doc("a")];
    const next = [doc("new"), ...shown];
    const pinned = new Set(shown.map((item) => item.id));
    expect(rowsInPlace(next, pinned).map((item) => item.id)).toEqual(["b", "a"]);
    expect(rowsInPlace(next, null).map((item) => item.id)).toEqual(["new", "b", "a"]);
    expect(rowsInPlace(shown, pinned)).toBe(shown); // nothing held: the same list
    const otherPage = [doc("x"), doc("y")];
    expect(rowsInPlace(otherPage, pinned)).toBe(otherPage); // a new page shows at once
    expect(rowsInPlace([doc("a")], pinned).map((item) => item.id)).toEqual(["a"]); // a removed row goes
  });

  it("holds rows from the table's pointer events", () => {
    const table = read("components/documents/DocumentsTable.tsx");
    expect(table).toMatch(/onPointerEnter=\{\(\) => setPinned\(new Set\(listed\.map/);
    expect(table).toMatch(/onPointerLeave=\{\(\) => setPinned\(null\)\}/);
  });
});

describe("motion", () => {
  const LAYOUT = /\b(width|height|top|left|right|bottom|inset|margin|padding|max-height|min-height|flex|grid)\b/;
  const cssFiles = (dir: string): string[] =>
    readdirSync(join(ROOT, dir)).flatMap((name) => {
      const path = join(dir, name);
      return statSync(join(ROOT, path)).isDirectory() ? cssFiles(path) : name.endsWith(".css") ? [path] : [];
    });

  it("animates no layout property in the documents screen or the shell", () => {
    for (const file of [...cssFiles("components/documents"), ...cssFiles("components/shell")]) {
      const css = read(file).replace(/\/\*[\s\S]*?\*\//g, "");
      for (const [, body] of css.matchAll(/@keyframes [\w-]+ \{([\s\S]*?)\n\}/g)) {
        for (const [, property] of (body ?? "").matchAll(/([\w-]+):/g)) expect(property, file).toMatch(/^(opacity|transform)$/);
      }
      for (const [, list] of css.matchAll(/transition(?:-property)?:([^;]*);/g)) expect(list, file).not.toMatch(LAYOUT);
    }
  });

  it("fades the new words in with opacity only, and not at all with reduced motion", () => {
    const css = read("components/documents/Documents.module.css");
    expect(css).toMatch(/@media \(prefers-reduced-motion: no-preference\) \{\s*\.statusLine > \* \{\s*animation: statusIn/);
    expect(css).toMatch(/@keyframes statusIn \{\s*from \{\s*opacity: 0;\s*\}\s*\}/);
  });
});
