// The time left beside a document being read (lib/timeLeft.ts, PRD 4 #13), with
// plain numbers: times are performance.now() milliseconds.

import { describe as group, expect, it } from "vitest";
import { ESTIMATING, STARTING, describe, hold, markReceived, receivedAt } from "@/lib/timeLeft";
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

// Changed on purpose (scan states, 16 Oct 2026): "About 2 min left", never "Ready in about"
// or "(rough guess)"; Taking longer than usual keeps the time beside it.
group("time left", () => {
  it("counts down: minutes from 60 s, then 5 s steps rounded up, then Almost done, never below", () => {
    const held = first(125);
    const at = (ms: number) => textAt(held, ms);
    expect([at(0), at(66_000), at(70_000)]).toEqual(["About 2 min left", "About 1 min left", "About 55 s left"]); // 59 s: 60
    expect([at(84_000), at(115_000)]).toEqual(["About 45 s left", "About 10 s left"]); // 41 s: 45
    expect([at(115_100), at(999_000)]).toEqual(["Almost done", "Almost done"]);
    expect([textAt(first(45), 0, true), textAt(first(45), 40_000, true)]).toEqual(["About 1 min left", "Almost done"]);
  });

  it("changes the words for screen readers only by the minute", () => {
    const spoken = [0, 10_000, 20_000, 40_000].map((ms) => describe(first(59), ms, false).spoken);
    expect(new Set(spoken)).toEqual(new Set(["About 1 minute left"]));
    expect(describe(first(150), 0, false).spoken).toBe("About 3 minutes left");
  });

  it("applies a lower estimate at once, absorbs a small rise, and says Taking longer for a big one", () => {
    const held = first(100); // 90 s shown at 10 s
    const lower = hold(held, value(80), "understanding", 10_000);
    expect([lower.seconds, lower.anchor]).toEqual([80, 10_000]);
    const small = hold(held, value(98), "understanding", 10_000); // within max(10 s, 20%)
    expect([small.seconds, small.anchor, small.rising]).toEqual([100, 0, false]);
    const rise = hold(held, value(115), "understanding", 10_000);
    expect(describe(rise, 10_000, false)).toEqual({
      state: "longer",
      text: "Taking longer than usual · About 2 min left",
      spoken: "Taking longer than usual. About 2 minutes left",
    });
    expect(textAt(hold(rise, value(110), "understanding", 20_000), 20_000)).toBe("About 2 min left");
    expect(hold(first(30, "reading"), value(300), "understanding", 1000).seconds).toBe(300); // a new step
  });

  it("says Taking longer once nothing has moved past slow_after_seconds", () => {
    const held = first(100, "understanding", { since_move_seconds: 15, slow_after_seconds: 20 });
    expect([describe(held, 4_000, false).state, describe(held, 6_000, false).state]).toEqual(["counting", "longer"]);
  });

  // Changed on purpose (scan states, 16 Oct 2026): a low-confidence number reads like any
  // estimate, "About 3 min left" ("about" says it is one); no "(rough guess)" any more.
  it("says a low-confidence number with About, like any estimate; Estimating only with none", () => {
    const low = describe(first(180, "reading", { confidence: "low" }), 0, false);
    expect(low).toEqual({ state: "counting", text: "About 3 min left", spoken: "About 3 minutes left" });
    const soon = describe(first(45, "understanding", { confidence: "low" }), 0, false);
    expect([soon.text, soon.spoken]).toEqual(["About 45 s left", "About 1 minute left"]);
    expect(textAt(first(5, "understanding", { confidence: "low" }), 0)).toBe("Almost done");
    expect(describe(first(180), 0, false).text).toBe("About 3 min left"); // high confidence
    expect([ESTIMATING.text, STARTING.text]).toEqual(["Estimating\u2026", "Starting\u2026"]);
  });

  // Changed on purpose (council 16 Oct 2026): checked at 35 s, within 10 s of the floor; at
  // 50 s the countdown is 20 s past it, which now says Taking longer (below).
  it("never counts below the time after the step's pages while preparing or reading", () => {
    expect(textAt(first(60, "reading", { after_seconds: 30 }), 35_000)).toBe("About 30 s left");
    expect(textAt(first(60, "preparing", { after_seconds: 30 }), 35_000)).toBe("About 30 s left");
    expect(textAt(first(60, "understanding", { after_seconds: 30 }), 50_000)).toBe("About 10 s left");
  });

  // Council 16 Oct 2026: past the step's end by more than 10 s, the floor is not a time left.
  it("says Taking longer once preparing or reading is more than 10 s past its expected end", () => {
    const reading = first(60, "reading", { after_seconds: 30 }); // the step ends at 30 s
    expect([describe(reading, 40_000, false).state, describe(reading, 40_100, false).state]).toEqual(["counting", "longer"]);
    expect(describe(first(60, "preparing", { after_seconds: 30 }), 41_000, false).state).toBe("longer");
    expect(describe(first(60, "understanding", { after_seconds: 30 }), 41_000, false).state).toBe("counting");
  });

  it("anchors each answer when it arrived", () => {
    const marked = value(40);
    markReceived([{ time_left: marked }, { time_left: null }], 1234);
    expect(receivedAt(marked)).toBe(1234);
    const merged = value(40); // a reply never marked: noted the first time it is shown
    expect(receivedAt(merged)).toBe(receivedAt(merged));
  });
});
