// The progress bar beside a document being read (lib/progress.ts, PRD 4 #13 fix),
// with plain numbers: times are performance.now() milliseconds.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TimeLeft } from "@/components/documents/TimeLeft";
import { advance, barValue, fractionAt, fractionDone, fractionShown } from "@/lib/progress";
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
  done_fraction: 0,
  ...more,
});

/** The percents shown for a run of fractions in one attempt (null: no estimate). */
function percents(steps: (number | null)[]): number[] {
  let shown: ShownProgress | null = null;
  const out: number[] = [];
  for (const step of steps) {
    shown = advance(shown, 1, step);
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
    const shown = advance(null, 1, 0.4);
    expect(advance(shown, 1, 0.3)).toBe(shown); // the same object: nothing to update
  });

  // Changed on purpose (council of 5 Oct 2026): taking longer no longer freezes the bar
  // where this mount had it (0 after a refresh); it shows the server's done_fraction,
  // which the server holds still, and never less than it.
  it("takes the server's done_fraction while taking longer, and never less live", () => {
    const stalled = value({ since_move_seconds: 1200, slow_after_seconds: 20, done_fraction: 0.36 });
    expect(fractionShown(stalled, "reading", 0, 400_000, true)).toBe(0.36); // not 30 + 400 s of nothing
    expect(fractionShown(value({ done_fraction: 0.4 }), "understanding", 0, 0, false)).toBe(0.4); // live 0.25
    expect(fractionShown(value({ done_fraction: 0.1 }), "understanding", 0, 30_000, false)).toBe(0.5);
    expect(percents([0.2, null, 0.29])).toEqual([20, 20, 29]);
  });

  it("stops at 99% until the document is ready", () => {
    expect(percents([0.5, 0.999, 1, 5])).toEqual([50, 99, 99, 99]);
    expect(barValue(99)).toBe(95);
  });

  it("starts a new attempt from that run's own value", () => {
    const first = advance(null, 1, 0.8);
    expect(advance(first, 2, null)).toEqual({ attempt: 2, percent: 0 });
    expect(advance(first, 2, 0.1)).toEqual({ attempt: 2, percent: 10 });
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

const render = (more: Partial<DocumentSummary>) => renderToStaticMarkup(createElement(TimeLeft, { document: doc(more) }));
// Taking longer than usual: 20 min with no page read, the server's done_fraction 36%.
const STALLED = { since_move_seconds: 1200, slow_after_seconds: 20, done_fraction: 0.36, elapsed_seconds: 1500 };

describe("the bar on screen", () => {
  // Changed on purpose (council of 5 Oct 2026): at 0 the percent text is empty, never a bare 0%.
  it("names the bar for its document and hides the percent and the counting text", () => {
    const html = render({});
    expect(html).toContain('<progress');
    expect(html).toContain('aria-label="Progress: Invoice 7.pdf"');
    expect(html).toContain('value="0"');
    expect(html).toMatch(/<span[^>]*aria-hidden="true"[^>]*><\/span>/);
    expect(html).not.toContain("0%");
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

describe("a refresh", () => {
  it("refresh while taking longer shows the server-derived percent, not 0", () => {
    const html = render({ progress: { step: "reading", done: 0, total: 1 }, time_left: value(STALLED) });
    expect(html).toContain(">36%</span>");
    expect(html).toContain("Taking longer than usual");
    expect(html).toContain('value="35"'); // the bar in 5% steps
    expect(html).not.toMatch(/aria-live|role="(status|timer|alert)"/);
  });

  it("shows the same percent on two fresh mounts at different times", () => {
    const reading = { step: "reading", done: 0, total: 1 } as const;
    const first = render({ progress: reading, time_left: value(STALLED) });
    const later = value({ ...STALLED, since_move_seconds: 1600, elapsed_seconds: 1900 }); // 400 s on, nothing moved
    expect(render({ progress: reading, time_left: later })).toBe(first);
  });

  it("never shows less than done_fraction, and a new attempt shows its own run's value", () => {
    const behind = value({ seconds: 900, done_fraction: 0.42 }); // live: 30 / 930
    expect(render({ progress: { step: "understanding", done: 0, total: 2 }, time_left: behind })).toContain(">42%<");
    const retry = value({ ...STALLED, done_fraction: 0.12 });
    expect(render({ attempts: 2, progress: { step: "reading", done: 0, total: 4 }, time_left: retry })).toContain(">12%<");
  });
});
