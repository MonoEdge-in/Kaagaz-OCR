// The progress bar beside a document being read (lib/progress.ts, PRD 4 #13 fix),
// with plain numbers: times are performance.now() milliseconds.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TimeLeft } from "@/components/documents/TimeLeft";
import { advance, barValue, fractionAt, fractionDone } from "@/lib/progress";
import type { ShownProgress } from "@/lib/progress";
import { STARTING, hold } from "@/lib/timeLeft";
import type { DocumentSummary, TimeLeft as TimeLeftValue } from "@/lib/types";

const value = (more: Partial<TimeLeftValue> = {}): TimeLeftValue => ({
  seconds: 90,
  since_move_seconds: 0,
  slow_after_seconds: 1000,
  items_left: 0,
  item_seconds: 0,
  after_seconds: 0,
  confidence: "low",
  elapsed_seconds: 30,
  ...more,
});

/** The percents shown for a run of fractions in one attempt (null: no estimate; "stall": taking longer). */
function percents(steps: (number | null | "stall")[]): number[] {
  let shown: ShownProgress | null = null;
  const out: number[] = [];
  for (const step of steps) {
    shown = step === "stall" ? advance(shown, 1, 0.9, true) : advance(shown, 1, step, false);
    out.push(shown.percent);
  }
  return out;
}

describe("progress", () => {
  it("is elapsed over elapsed plus left, with no step weights", () => {
    expect(fractionDone(30, 90)).toBe(0.25);
    expect([fractionDone(0, 0), fractionDone(Number.NaN, 10), fractionDone(-5, 10)]).toEqual([0, 0, 0]);
    const held = hold(null, value(), "understanding", 0); // 30 s in, 90 s left
    expect(fractionAt(held, 0)).toBe(0.25);
    expect(fractionAt(held, 30_000)).toBe(0.5); // counts on between answers: 60 s in, 60 s left
    const reading = hold(null, value({ after_seconds: 80 }), "reading", 0);
    expect(fractionAt(reading, 30_000)).toBe(60 / 140); // never below the time after reading
  });

  it("never goes back, even when a new estimate says more is left", () => {
    expect(percents([0.1, 0.3, 0.2, 0.5, 0.45])).toEqual([10, 30, 30, 50, 50]);
    const shown = advance(null, 1, 0.4, false);
    expect(advance(shown, 1, 0.3, false)).toBe(shown); // the same object: nothing to update
  });

  it("holds still while it is taking longer than usual, and with no estimate", () => {
    expect(percents([0.2, "stall", "stall", null, 0.3])).toEqual([20, 20, 20, 20, 30]);
  });

  it("stops at 99% until the document is ready", () => {
    expect(percents([0.5, 0.999, 1, 5])).toEqual([50, 99, 99, 99]);
    expect(barValue(99)).toBe(95);
  });

  it("starts again from 0 on a new attempt", () => {
    const first = advance(null, 1, 0.8, false);
    expect(advance(first, 2, null, false)).toEqual({ attempt: 2, percent: 0 });
    expect(advance(first, 2, 0.1, false)).toEqual({ attempt: 2, percent: 10 });
  });

  it("moves the bar in 5% steps, never ahead of the percent shown", () => {
    expect([0, 4, 5, 42, 45, 99].map(barValue)).toEqual([0, 0, 5, 40, 45, 95]);
  });
});

const doc = (more: Partial<DocumentSummary>): DocumentSummary =>
  ({
    status: "clean",
    progress: { step: "scanning", done: 0, total: 1 },
    time_left: null,
    attempts: 1,
    deleted_at: null,
    display_name: "Invoice 7.pdf",
    ...more,
  }) as DocumentSummary;

describe("the bar on screen", () => {
  it("names the bar for its document and hides the percent and the counting text", () => {
    const html = renderToStaticMarkup(createElement(TimeLeft, { document: doc({}) }));
    expect(html).toContain('<progress');
    expect(html).toContain('aria-label="Progress: Invoice 7.pdf"');
    expect(html).toContain('value="0"');
    expect(html).toMatch(/<span[^>]*aria-hidden="true"[^>]*>0%<\/span>/);
    expect(html).toContain(STARTING.text);
    expect(html).not.toMatch(/aria-live|role="(status|timer|alert)"/);
  });

  it("shows a rough time with its percent from preparing on, and nothing in Trash", () => {
    const preparing = doc({ progress: { step: "preparing", done: 1, total: 4 }, time_left: value() });
    const html = renderToStaticMarkup(createElement(TimeLeft, { document: preparing }));
    expect(html).toContain("25%");
    expect(html).toContain("About 2 min (rough guess)");
    expect(renderToStaticMarkup(createElement(TimeLeft, { document: doc({ deleted_at: "2026-10-04T10:00:00Z" }) }))).toBe("");
  });
});
