// DropZone, StageProgress, FiledPath, BatchMeter, Timeline and EmptyState,
// against Components.html sections 13, 14 and 16 and desktop/Upload.html.

import { createElement as h } from "react";
import { describe, expect, it } from "vitest";
import { BatchMeter, meterParts, meterWords } from "@/components/ui/BatchMeter/BatchMeter";
import { Button } from "@/components/ui/Button/Button";
import { carriesFiles, DropZone } from "@/components/ui/DropZone/DropZone";
import { EmptyState, Ledger } from "@/components/ui/EmptyState/EmptyState";
import { FiledPath, overallPercent, StageProgress, stageWords } from "@/components/ui/StageProgress/StageProgress";
import { Timeline } from "@/components/ui/Timeline/Timeline";
import { render } from "./kit-render";


describe("DropZone", () => {
  const html = render(DropZone, {
    title: "Drop up to 100 files here, or choose files",
    help: "PDF, JPG, PNG, Excel, CSV · up to 50 MB each",
    actions: h(Button, { variant: "primary", icon: "file" }, "Choose files"),
    aside: "DEPARTMENT",
    onFiles: () => {},
  });

  it("is a named area with the drawing, the words, the buttons and the side slot", () => {
    expect(html).toMatch(/^<section class="_drop_\w+" aria-label="Add files"><div class="_art_\w+" aria-hidden="true">/);
    expect(html).toContain('<p class="k-h3">Drop up to 100 files here, or choose files</p><p class="k-small k-muted">PDF, JPG, PNG, Excel, CSV · up to 50 MB each</p>');
    expect(html).toMatch(/<div class="_actions_\w+"><button[^>]*>.*Choose files<\/button><\/div><\/div><div class="_aside_\w+">DEPARTMENT<\/div><\/section>$/);
  });

  it("never lists HEIC", () => {
    expect(html).not.toContain("HEIC");
  });

  it("lights up only for files, not for text dragged from the page", () => {
    expect(carriesFiles(["Files"])).toBe(true);
    expect(carriesFiles(["text/plain", "text/uri-list"])).toBe(false);
  });
});

describe("StageProgress", () => {
  it("sends with a real percentage, the first segment active", () => {
    const progress = { stage: "sending" as const, percent: 60 };
    const html = render(StageProgress, { name: "Bank Statement HDFC Sept.xlsx", progress });
    expect(html).toContain('<span class="k-small k-muted">Sending 60%</span>');
    expect(html).toContain('role="progressbar" aria-label="Sending 60%" aria-valuemin="0" aria-valuemax="100" aria-valuenow="15"');
    expect(html).toMatch(/<span class="_stage_\w+ _active_\w+"><i style="width:60%"><\/i><\/span><span class="_stage_\w+"><\/span><span class="_stage_\w+"><\/span>/);
    expect(html).toMatch(/<span class="_active_\w+">Sending<\/span><span>Saving<\/span><span>Reading<\/span>/);
  });

  it("saves without making up a number", () => {
    const html = render(StageProgress, { name: "NA Permission Plot 7.pdf", progress: { stage: "saving" } });
    expect(html).toMatch(/<span class="_stage_\w+ _active_\w+ _moving_\w+"><i><\/i><\/span>/);
    expect(html).toMatch(/<span class="_done_\w+">Sent<\/span><span class="_active_\w+">Saving<\/span><span>Reading<\/span>/);
    expect(html).toContain('aria-valuenow="25"');
  });

  it("reads page by page and fills only at the last page", () => {
    const html = render(StageProgress, { name: "7/12 Survey 44 Wakad (2).pdf", progress: { stage: "reading", done: 4, total: 12 } });
    expect(html).toContain('<span class="k-small k-muted">Reading 4 of 12 pages</span>');
    expect(html).toMatch(/<span class="_done_\w+">Sent<\/span><span class="_done_\w+">Saved<\/span><span class="_active_\w+">Reading 4 \/ 12<\/span>/);
    expect(html).toContain('style="width:33.3%"');
  });

  it("reads a spreadsheet as a whole, with no page count", () => {
    const html = render(StageProgress, { name: "Sales.xlsx", progress: { stage: "reading", done: 0, total: 0 } });
    expect(html).toMatch(/_stage_\w+ _active_\w+ _moving_\w+"><i><\/i>/);
    expect(html).toContain('<span class="k-small k-muted">Reading</span>');
  });

  it("never reaches 100 before the file is ready", () => {
    expect(overallPercent({ stage: "sending", percent: 0 })).toBe(0);
    expect(overallPercent({ stage: "sending", percent: 100 })).toBe(25);
    expect(overallPercent({ stage: "sending", percent: 140 })).toBe(25);
    expect(overallPercent({ stage: "saving" })).toBe(25);
    expect(overallPercent({ stage: "reading", done: 0, total: 12 })).toBe(50);
    expect(overallPercent({ stage: "reading", done: 6, total: 12 })).toBe(75);
    expect(overallPercent({ stage: "reading", done: 12, total: 12 })).toBe(99);
    expect(overallPercent({ stage: "reading", done: 0, total: 0 })).toBe(50);
    expect(stageWords({ stage: "sending", percent: 59.6 })).toBe("Sending 60%");
    expect(stageWords({ stage: "reading", done: 1200, total: 1500 })).toBe("Reading 1,200 of 1,500 pages");
  });

  it("shows where a ready file went", () => {
    expect(render(FiledPath, null, "Accounts / Invoices / Mehta Textiles")).toMatch(
      /^<span class="_filed_\w+"><svg[^>]*><path d="M5 12h14M13 6l6 6-6 6"><\/path><\/svg><b>Accounts \/ Invoices \/ Mehta Textiles<\/b><\/span>$/,
    );
  });
});

describe("BatchMeter", () => {
  const counts = { ready: 41, reading: 5, waiting: 2, dup: 1 };

  it("splits one bar by what each file is doing, said in words", () => {
    const html = render(BatchMeter, { counts });
    expect(html).toMatch(/^<div class="_meter_\w+" role="img" aria-label="41 ready, 5 reading, 2 waiting, 1 already uploaded">/);
    expect(html).toMatch(/<span class="_ready_\w+" style="width:83.7%"><\/span><span class="_reading_\w+" style="width:10.2%"><\/span>/);
    expect(html).toMatch(/<li><i class="_dup_\w+"><\/i>Already uploaded 1<\/li><\/ul>$/);
  });

  it("leaves out parts with no files", () => {
    expect(meterParts({ ready: 3, failed: 0 })).toEqual([{ part: "ready", count: 3, width: 100 }]);
    expect(meterParts({})).toEqual([]);
    expect(meterWords({ ready: 1, failed: 2 })).toBe("1 ready, 2 failed");
    expect(render(BatchMeter, { counts: {} })).toContain('aria-label="No files yet"');
  });
});

describe("Timeline", () => {
  it("draws who did what and when, with the tone of each dot", () => {
    const html = render(Timeline, {
      entries: [
        { key: "1", icon: "eye", text: "Priya viewed", when: "today 11:04" },
        { key: "2", icon: "versions", tone: "accent", text: "Rahul linked version 2", when: "today 10:42" },
        { key: "3", icon: "tag", tone: "review", text: "Anita changed the label", when: "5 Oct 2026, 16:20" },
      ],
    });
    expect(html).toMatch(/^<ol class="_timeline_\w+"><li><span class="_dot_\w+"><svg/);
    expect(html).toMatch(/<span class="_dot_\w+ _accent_\w+">/);
    expect(html).toMatch(/<span class="_dot_\w+ _review_\w+">/);
    expect(html).toMatch(/<span class="_text_\w+"><span>Priya viewed<\/span><span class="_when_\w+">today 11:04<\/span><\/span>/);
  });
});

describe("EmptyState", () => {
  it("draws two ledger sheets, what is missing, what to do and the action", () => {
    const html = render(
      EmptyState,
      { title: "No documents in this folder yet", action: h(Button, { variant: "primary", icon: "upload" }, "Upload to Survey 44, Wakad") },
      "Drop bills, agreements or 7/12 extracts here.",
    );
    expect(html).toMatch(/^<div class="_empty_\w+"><div class="_stack_\w+"><span class="_ledger_\w+ _back_\w+" aria-hidden="true"><\/span><span class="_ledger_\w+ _front_\w+" aria-hidden="true"><\/span><\/div>/);
    expect(html).toContain('<h3 class="k-h3">No documents in this folder yet</h3><p>Drop bills, agreements or 7/12 extracts here.</p><button');
  });

  it("draws the ledger on its own", () => {
    expect(render(Ledger)).toMatch(/^<span class="_ledger_\w+" aria-hidden="true"><\/span>$/);
  });
});
