// #468 B4: leaving the page while a file is still waiting or sending asks first (beforeunload),
// and after a reload the tray says plainly how many files were not sent and must be chosen again.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BatchTray } from "@/components/documents/UploadArea";
import { keptCounts, notSentText, summaryText, trayCounts } from "@/components/documents/batchTray";
import { buildTree } from "@/components/documents/folderTree";
import type { UploadState } from "@/components/documents/logic";
import { uploadsAtRisk } from "@/components/documents/uploadQueue";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import type { DocumentSummary } from "@/lib/types";

const ID = "6f1c2d3e-4a5b-4c6d-8e7f-901a2b3c4d5e";
const row = (key: string, state: UploadState): UploadEntry => ({ key, name: `${key}.pdf`, size: 1000, batchId: ID, state });
const readyDoc = { id: "d", status: "ready" } as DocumentSummary;

describe("leaving while a file is waiting or sending asks first", () => {
  it("is at risk only while a file is waiting, sending, offline or saving", () => {
    for (const state of [
      { kind: "waiting" },
      { kind: "sending", fraction: 0.4 },
      { kind: "offline", fraction: 0.4 },
      { kind: "saving" },
    ] as UploadState[]) {
      expect(uploadsAtRisk([row("a", { kind: "done", documentId: "d" }), row("b", state)]), state.kind).toBe(true);
    }
    // The server has the file once it is read, done, refused or failed: leaving loses nothing.
    expect(uploadsAtRisk([])).toBe(false);
    expect(uploadsAtRisk([row("a", { kind: "reading", documentId: "d", document: readyDoc })])).toBe(false);
    expect(uploadsAtRisk([row("a", { kind: "done", documentId: "d" })])).toBe(false);
    expect(uploadsAtRisk([row("a", { kind: "error", message: "x" })])).toBe(false);
  });

  it("adds the warning while at risk and removes it when the files are sent", () => {
    const area = readFileSync(join(__dirname, "..", "components/documents/UploadArea.tsx"), "utf8");
    expect(area).toMatch(
      /const atRisk = uploadsAtRisk\(queue\.entries\);\s*useEffect\(\(\) => \{\s*if \(!atRisk\) return;[\s\S]{0,300}window\.addEventListener\("beforeunload", warn\);\s*return \(\) => window\.removeEventListener\("beforeunload", warn\);\s*\}, \[atRisk\]\);/,
    );
    // Changed on purpose (#468, builder A): a page left on purpose (leavePage) is not asked about.
    expect(area).toMatch(/const warn = \(event: BeforeUnloadEvent\) => \{\s*if \(leavingPage\(\)\) return;[^\n]*\s*event\.preventDefault\(\);/);
  });
});

describe("files not sent before a reload: counted on their own, with what to do", () => {
  it("keeps not sent apart from failed, and counts it in the drop", () => {
    // Changed on purpose (#468): a reload's not-sent files were folded into "failed".
    const counts = trayCounts([row("ready", { kind: "done", documentId: "d", document: readyDoc })], { already: 0, notSent: 2 });
    expect(counts).toMatchObject({ total: 3, ready: 1, failed: 0, notSent: 2 });
    expect(summaryText(counts)).toBe("Batch of 3 files · 1 ready");
    expect(notSentText(counts)).toBe("2 not sent — choose them again");
    expect(notSentText({ ...counts, notSent: 1 })).toBe("1 not sent — choose it again");
    expect(notSentText({ ...counts, notSent: 0 })).toBeNull();
  });

  it("in this tab the rows say it themselves (with Retry), so nothing is counted twice", () => {
    const counts = trayCounts([row("e", { kind: "error", message: "Not sent.", retry: true })], { already: 0, notSent: 1 });
    expect(counts).toMatchObject({ total: 1, failed: 1, notSent: 0 });
  });

  it("shows the line in the tray", () => {
    const html = renderToStaticMarkup(
      createElement(BatchTray, {
        entries: [row("ready", { kind: "done", documentId: "d", document: readyDoc })],
        views: {},
        stored: [{ id: ID, at: 0, already: 0, notSent: 3 }],
        onClearFinished: () => undefined,
        onRestore: () => undefined,
        onRetry: () => undefined,
        tree: buildTree([]),
        onUndone: () => undefined,
        canReread: true,
      }),
    );
    expect(html).toContain("<strong>3 not sent — choose them again</strong>");
  });

  it("remembers a reloaded drop's counts as they were, since its rows never hold these two", () => {
    const batch = { id: ID, at: 0, already: 1, notSent: 3 };
    const rows = [row("ready", { kind: "done", documentId: "d", document: readyDoc })];
    expect(keptCounts(batch, rows, true)).toEqual(batch);
    expect(keptCounts(batch, [], false)).toEqual(batch);
    // A drop of this tab: counted from its rows.
    expect(keptCounts(batch, [...rows, row("e", { kind: "error", message: "x" })], false)).toEqual({ ...batch, already: 0, notSent: 1 });
  });
});
