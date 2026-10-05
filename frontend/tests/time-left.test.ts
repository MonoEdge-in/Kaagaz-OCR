// The time left beside a document being read (lib/timeLeft.ts, PRD 4 #13), with
// plain numbers: times are performance.now() milliseconds.

import { describe as group, expect, it } from "vitest";
import { ESTIMATING, STARTING, WAITING, describe, hold, markReceived, receivedAt } from "@/lib/timeLeft";
import type { Held } from "@/lib/timeLeft";
import type { TimeLeft } from "@/lib/types";

const base = { since_move_seconds: 0, slow_after_seconds: 1000, items_left: 0, item_seconds: 0, after_seconds: 0, elapsed_seconds: 0, done_fraction: 0 };
const value = (seconds: number, more: Partial<TimeLeft> = {}): TimeLeft => ({
  ...base,
  seconds,
  confidence: "high",
  ...more,
});
const first = (seconds: number, step: Held["step"] = "understanding", more: Partial<TimeLeft> = {}) =>
  hold(null, value(seconds, more), step, 0);
const textAt = (held: Held, ms: number, reduced = false) => describe(held, ms, reduced).text;

group("time left", () => {
  it("counts down: minutes from 60 s, then 5 s steps rounded up, then Almost done, never below", () => {
    const held = first(125);
    const at = (ms: number) => textAt(held, ms);
    expect([at(0), at(66_000), at(70_000)]).toEqual(["Ready in about 2 min", "Ready in about 1 min", "Ready in about 55 s"]); // 59 s: 60
    expect([at(84_000), at(115_000)]).toEqual(["Ready in about 45 s", "Ready in about 10 s"]); // 41 s: 45
    expect([at(115_100), at(999_000)]).toEqual(["Almost done", "Almost done"]);
    expect([textAt(first(45), 0, true), textAt(first(45), 40_000, true)]).toEqual(["Ready in about 1 min", "Almost done"]);
  });

  it("changes the words for screen readers only by the minute", () => {
    const spoken = [0, 10_000, 20_000, 40_000].map((ms) => describe(first(59), ms, false).spoken);
    expect(new Set(spoken)).toEqual(new Set(["Ready in less than a minute"]));
    expect(describe(first(150), 0, false).spoken).toBe("Ready in about 3 minutes");
  });

  it("applies a lower estimate at once, absorbs a small rise, and says Taking longer for a big one", () => {
    const held = first(100); // 90 s shown at 10 s
    const lower = hold(held, value(80), "understanding", 10_000);
    expect([lower.seconds, lower.anchor]).toEqual([80, 10_000]);
    const small = hold(held, value(98), "understanding", 10_000); // within max(10 s, 20%)
    expect([small.seconds, small.anchor, small.rising]).toEqual([100, 0, false]);
    const rise = hold(held, value(115), "understanding", 10_000);
    expect(describe(rise, 10_000, false).state).toBe("longer");
    expect(textAt(hold(rise, value(110), "understanding", 20_000), 20_000)).toBe("Ready in about 2 min");
    expect(hold(first(30, "reading"), value(300), "understanding", 1000).seconds).toBe(300); // a new step
  });

  it("says Taking longer once nothing has moved past slow_after_seconds", () => {
    const held = first(100, "understanding", { since_move_seconds: 15, slow_after_seconds: 20 });
    expect([describe(held, 4_000, false).state, describe(held, 6_000, false).state]).toEqual(["counting", "longer"]);
  });

  // Changed on purpose (council of 4 Oct 2026, progress %): low confidence said
  // "Estimating..." for ever; now it shows the number, labelled a rough guess.
  it("labels a low-confidence number a rough guess; Estimating only with no estimate at all", () => {
    const rough = describe(first(180, "reading", { confidence: "low" }), 0, false);
    expect(rough).toEqual({ state: "rough", text: "About 3 min (rough guess)", spoken: "Rough guess: ready in about 3 minutes" });
    const soon = describe(first(45, "understanding", { confidence: "low" }), 0, false);
    expect([soon.text, soon.spoken]).toEqual(["About 45 s (rough guess)", "Rough guess: ready in less than a minute"]);
    expect(textAt(first(5, "understanding", { confidence: "low" }), 0)).toBe("Almost done");
    expect(describe(first(180), 0, false).text).toBe("Ready in about 3 min"); // high confidence
    expect([ESTIMATING.text, STARTING.text]).toEqual(["Estimating\u2026", "Starting\u2026"]);
  });

  it("never counts below the time after the step's pages while preparing or reading", () => {
    expect(textAt(first(60, "reading", { after_seconds: 30 }), 50_000)).toBe("Ready in about 30 s");
    expect(textAt(first(60, "preparing", { after_seconds: 30 }), 50_000)).toBe("Ready in about 30 s");
    expect(textAt(first(60, "understanding", { after_seconds: 30 }), 50_000)).toBe("Ready in about 10 s");
  });

  it("anchors each answer when it arrived", () => {
    const marked = value(40);
    markReceived([{ time_left: marked }, { time_left: null }], 1234);
    expect(receivedAt(marked)).toBe(1234);
    const merged = value(40); // a reply never marked: noted the first time it is shown
    expect(receivedAt(merged)).toBe(receivedAt(merged));
    expect(WAITING.text).toBe("Waiting to start");
  });
});
