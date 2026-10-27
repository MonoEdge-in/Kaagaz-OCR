// The operator's Overview, Quality and Compare models logic (operator insights, council
// 11 Oct 2026, F1-F4): days, totals, change and rate words, unit costs, the forecast,
// every "Needs attention" flag at and just below its PROVISIONAL threshold, the CSV and
// the board; and the two small pictures, hidden from screen readers, never a canvas.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ShareBar, Sparkline } from "@/components/operator/Charts";
import type { SharePart } from "@/components/operator/Charts";
import {
  ATTENTION,
  BOARD_MIN_DOCUMENTS,
  INSIGHT_PERIODS,
  K_MIN,
  attentionItems,
  boardBest,
  boardRows,
  changeLine,
  changeText,
  csvOf,
  dayList,
  monthForecast,
  orgFlags,
  periodTotals,
  realMonthForecast,
  realMonthSpend,
  rateText,
  riskOrder,
  series,
  sparkOf,
  unknownText,
  usdPer1000Pages,
  usdPer100Questions,
} from "@/components/operator/insights";
import type { FlagCode } from "@/components/operator/insights";
import type { BoardRow, InsightDay, InsightsReply, OrgInsight, PlatformHealth, QualityReply, QualityRow } from "@/lib/types";

const day = (overrides: Partial<InsightDay> = {}): InsightDay => ({
  organisation_id: "a",
  day: "2026-10-11",
  uploads: 0,
  sign_ins: 0,
  questions: 0,
  corrections: 0,
  runs: 0,
  runs_failed: 0,
  pages: 0,
  scan_pages: 0,
  cached_pages: 0,
  cost_usd: null,
  cost_scan_usd: null,
  cost_documents_usd: null,
  cost_questions_usd: null,
  cost_estimated: false,
  cost_part_unknown: false,
  pages_read: 0,
  pages_reread: 0,
  pages_read_costed: 0,
  cost_real_usd: null,
  cost_scan_real_usd: null,
  cost_answer_real_usd: null,
  cost_unknown_calls: 0,
  scan_unknown_calls: 0,
  answer_unknown_calls: 0,
  ...overrides,
});

const org = (overrides: Partial<OrgInsight> = {}): OrgInsight => ({
  organisation_id: "a",
  active_users_7d: 0,
  active_users_30d: 0,
  active_users_prev_30d: 0,
  answers_30d: 0,
  not_found_30d: null,
  open_review: 0,
  oldest_open_review_at: null,
  ai_budget_usd: null,
  month_cost_usd: null,
  month_cost_estimated: false,
  month_cost_part_unknown: false,
  last_active_day: null,
  ...overrides,
});

// A 7-day period: 5 to 11 Oct, the period before 28 Sep to 4 Oct.
const reply = (overrides: Partial<InsightsReply> = {}): InsightsReply => ({
  from: "2026-10-05",
  to: "2026-10-11",
  previous_from: "2026-09-28",
  ai_enabled: true,
  month: { from: "2026-10-01", today: "2026-10-11", days_in_month: 31 },
  organisations: [],
  daily: [],
  ...overrides,
});

// A 30-day period: 12 Sep to 11 Oct, the period before 13 Aug to 11 Sep.
const reply30 = (daily: InsightDay[]) => reply({ from: "2026-09-12", previous_from: "2026-08-13", daily });

const qualityRow = (overrides: Partial<QualityRow> = {}): QualityRow => ({
  organisation_id: null,
  runs: 0,
  runs_failed: 0,
  runs_failed_platform: 0,
  runs_degraded: 0,
  documents_run: 0,
  pages_cells: 0,
  pages_text_layer: 0,
  pages_ocr: 0,
  pages_blank: 0,
  pages_ai: 0,
  pages_local: 0,
  pages_cached: 0,
  pages_unreadable: 0,
  ready_documents: 0,
  ready_p50_seconds: null,
  ready_p95_seconds: null,
  answers: 0,
  answered: null,
  not_found: null,
  unchecked: null,
  answers_verified: null,
  answers_fixed_query: null,
  citations: null,
  feedback: 0,
  feedback_up: null,
  values_total: 0,
  values_needs_review: null,
  corrections: 0,
  corrections_second: null,
  corrections_typed: null,
  ...overrides,
});

const quality = (platform: Partial<QualityRow>): QualityReply => ({
  from: "2026-10-05",
  to: "2026-10-11",
  ai_enabled: true,
  platform: qualityRow(platform),
  organisations: [],
});

const NOW = new Date("2026-10-11T12:00:00Z");

const health = (overrides: Partial<PlatformHealth> = {}): PlatformHealth => ({
  organisations: [],
  totals: { documents_by_status: {}, failed_by_code: {}, in_progress: 0, oldest_in_progress_seconds: null },
  purges: { purged: 0, last_purged_at: null, swept: 0, last_swept_at: null },
  ...overrides,
});

const board = (overrides: Partial<BoardRow> = {}): BoardRow => ({
  model: "google/gemini-3.1-pro-preview",
  documents: 40,
  runs: 40,
  runs_failed: 0,
  runs_degraded: 0,
  pages_ai: 0,
  pages_local: 0,
  pages_cached: 0,
  seconds_per_page_p50: null,
  seconds_per_page_p95: null,
  calls: 0,
  scan_calls: 0,
  cached_scan_calls: 0,
  input_tokens: 0,
  output_tokens: 0,
  cost_usd: null,
  scan_cost_usd: null,
  cost_estimated: false,
  cost_part_unknown: false,
  ocr_pages: 0,
  pages_disagree: null,
  pages_refused: null,
  pages_fell_back: null,
  pages_unpinned: null,
  ...overrides,
});

const codes = (o: OrgInsight, r: InsightsReply = reply(), now = NOW): FlagCode[] => orgFlags(o, r, now).map((f) => f.code);

describe("constants", () => {
  it("keeps the decided periods, K and board minimum, and the PROVISIONAL thresholds", () => {
    expect(INSIGHT_PERIODS).toEqual([7, 30, 90]);
    expect(K_MIN).toBe(10);
    expect(BOARD_MIN_DOCUMENTS).toBe(30);
    expect(ATTENTION).toEqual({
      budgetCheck: 0.8, budgetAct: 1, quietDays: 14, activityDrop: 0.5, activityMinBase: 10,
      notFoundShare: 0.3, failedPlatformShare: 0.1, degradedShare: 0.05, reviewOldDays: 7,
      spikeTimesMedian: 2, spikeFloorUsd: 1, queueOldMinutes: 30,
    });
  });
});

describe("dayList", () => {
  it("includes both ends across a month end", () => {
    expect(dayList("2026-09-29", "2026-10-02")).toEqual(["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]);
  });

  it("crosses a year end and a leap day", () => {
    expect(dayList("2025-12-31", "2026-01-01")).toEqual(["2025-12-31", "2026-01-01"]);
    expect(dayList("2028-02-28", "2028-03-01")).toEqual(["2028-02-28", "2028-02-29", "2028-03-01"]);
  });

  it("gives one day for the same day, none backwards or for a bad date", () => {
    expect(dayList("2026-10-11", "2026-10-11")).toEqual(["2026-10-11"]);
    expect(dayList("2026-10-02", "2026-10-01")).toEqual([]);
    expect(dayList("not a day", "2026-10-01")).toEqual([]);
  });

  it("counts 90 days with no drift over a clock change (UTC on the strings)", () => {
    const days = dayList("2026-01-01", "2026-03-31");
    expect(days).toHaveLength(90);
    expect(days.at(-1)).toBe("2026-03-31");
    expect(new Set(days).size).toBe(90);
  });
});

describe("series", () => {
  const daily = [
    day({ day: "2026-10-01", uploads: 2, cost_usd: 0.5 }),
    day({ day: "2026-10-03", uploads: 3, cost_usd: null }),
    day({ organisation_id: "b", day: "2026-10-01", uploads: 4, cost_usd: 1 }),
  ];
  const days = dayList("2026-10-01", "2026-10-04");

  it("fills days without a row with 0 and sums every organisation without one", () => {
    expect(series(daily, days, "uploads")).toEqual([6, 0, 3, 0]);
    expect(series(daily, days, "cost_usd")).toEqual([1.5, 0, 0, 0]);
  });

  it("keeps to one organisation when asked", () => {
    expect(series(daily, days, "uploads", "a")).toEqual([2, 0, 3, 0]);
    expect(series(daily, days, "uploads", "c")).toEqual([0, 0, 0, 0]);
  });

  it("ignores rows outside the days", () => {
    expect(series(daily, ["2026-10-03"], "uploads")).toEqual([3]);
  });
});

describe("periodTotals", () => {
  it("sums the window only and counts an organisation active on an upload, a question or a run", () => {
    const totals = periodTotals(
      [
        day({ day: "2026-10-04", uploads: 100 }), // before the window
        day({ day: "2026-10-05", uploads: 1, pages: 10, scan_pages: 4, cached_pages: 2, runs: 2, runs_failed: 1 }),
        day({ organisation_id: "b", day: "2026-10-11", questions: 3 }),
        day({ organisation_id: "c", day: "2026-10-06", sign_ins: 5 }), // signing in alone is not use
        day({ organisation_id: "d", day: "2026-10-07", runs: 1 }),
        day({ organisation_id: "e", day: "2026-10-12", uploads: 9 }), // after the window
      ],
      "2026-10-05",
      "2026-10-11",
    );
    expect(totals).toEqual({
      activeOrganisations: 3, uploads: 1, pages: 10, questions: 3, runs: 3, runsFailed: 1, scanPages: 4, cachedPages: 2,
      costUsd: null, costDocumentsUsd: null, costQuestionsUsd: null, costEstimated: false, costPartUnknown: false,
      pagesRead: 0, pagesReread: 0, pagesReadCosted: 0, costRealUsd: null, costScanRealUsd: null, costAnswerRealUsd: null,
      unknownCalls: 0, scanUnknownCalls: 0, answerUnknownCalls: 0,
    });
  });

  // Tushar, 16 Oct 2026: the real figures (0032), pages read from the ledger, re-reads apart.
  it("sums the pages models read, the re-reads apart, and only the cost OpenRouter reported", () => {
    const totals = periodTotals(
      [
        day({ day: "2026-10-05", pages_read: 20, pages_reread: 3, pages_read_costed: 18, cost_real_usd: 0.5,
          cost_scan_real_usd: 0.4, cost_answer_real_usd: null, cost_unknown_calls: 2, scan_unknown_calls: 2 }),
        day({ organisation_id: "b", day: "2026-10-06", pages_read: 7, cost_real_usd: 0.25, cost_answer_real_usd: 0.1,
          cost_unknown_calls: 1, answer_unknown_calls: 1 }),
      ],
      "2026-10-05",
      "2026-10-11",
    );
    expect([totals.pagesRead, totals.pagesReread, totals.pagesReadCosted]).toEqual([27, 3, 18]);
    expect([totals.costRealUsd, totals.costScanRealUsd, totals.costAnswerRealUsd]).toEqual([0.75, 0.4, 0.1]);
    expect([totals.unknownCalls, totals.scanUnknownCalls, totals.answerUnknownCalls]).toEqual([3, 2, 1]);
    expect(totals.activeOrganisations).toBe(2); // pages read is use, even with no counted run
  });

  it("says a tile's change in plain words, and nothing when both periods are 0", () => {
    expect(changeLine(12, 10, "count", 30)).toBe("Up 20% on the previous 30 days");
    expect(changeLine(5, 5, "count", 30)).toBe("Same as the previous 30 days");
    expect(changeLine(3, 0, "count", 7)).toBe("None in the previous 7 days");
    expect(changeLine(9, 4, "count", 30)).toBe("Not enough earlier data to compare");
    expect(changeLine(0, 0, "count", 30)).toBeNull();
  });

  it("draws a day line only with 3 or more days of real values, never a made-up one", () => {
    expect(sparkOf([0, 0, 1, 0, 2])).toBeUndefined();
    expect(sparkOf([1, 0, 2, 0, 3])).toEqual([1, 0, 2, 0, 3]);
    expect(sparkOf([])).toBeUndefined();
  });

  it("says calls of unknown cost in words, and nothing for none", () => {
    expect(unknownText(12)).toBe("cost not known for 12 calls");
    expect(unknownText(1)).toBe("cost not known for 1 call");
    expect(unknownText(0)).toBeNull();
  });

  it("forecasts the month from real cost only with 7 days of it", () => {
    const month = { from: "2026-10-01", today: "2026-10-10", days_in_month: 31 };
    const days = (count: number) =>
      Array.from({ length: count }, (_, i) => day({ day: `2026-10-${String(i + 1).padStart(2, "0")}`, cost_real_usd: 1, cost_usd: 9 }));
    const covered = { from: "2026-09-11", to: "2026-10-10", previous_from: "2026-08-12", month };
    expect(realMonthForecast(reply({ ...covered, daily: days(7) }))).toBeCloseTo((7 / 10) * 31); // real $7, never the $63
    expect(realMonthForecast(reply({ ...covered, daily: days(6) }))).toBeNull();
    // Changed on purpose (real figures, 16 Oct 2026): the server sends the month from its first
    // day for a short period too, so a 7-day period forecasts as well.
    expect(realMonthForecast(reply({ ...covered, previous_from: "2026-10-03", daily: days(7) }))).toBeCloseTo((7 / 10) * 31);
  });

  it("sums one organisation's real spend this month, counts the calls of unknown cost and never guesses", () => {
    const daily = [
      day({ day: "2026-10-02", cost_real_usd: 2, cost_usd: 5, cost_unknown_calls: 3 }),
      day({ day: "2026-10-03", cost_real_usd: null, cost_usd: 4, cost_unknown_calls: 1 }), // estimated only
      day({ day: "2026-09-30", cost_real_usd: 50, cost_unknown_calls: 9 }), // last month
      day({ organisation_id: "b", day: "2026-10-02", cost_real_usd: 7 }),
    ];
    expect(realMonthSpend(reply({ daily }), 100, "a")).toEqual({ usd: 2, unknownCalls: 4, forecast: { usd: null, passDay: null } });
    expect(realMonthSpend(reply({ daily: [day({ cost_real_usd: null, cost_unknown_calls: 2 })] }), null, "a").usd).toBeNull();
  });

  it("sums the known costs and or-s the estimated and part-unknown flags", () => {
    const totals = periodTotals(
      [
        day({ day: "2026-10-05", cost_usd: 1.25, cost_documents_usd: 1, cost_questions_usd: null, cost_estimated: true }),
        day({ day: "2026-10-06", cost_usd: 0.75, cost_documents_usd: null, cost_questions_usd: 0.75, cost_part_unknown: true }),
        day({ day: "2026-10-07", cost_usd: null }),
      ],
      "2026-10-05",
      "2026-10-11",
    );
    expect(totals.costUsd).toBe(2);
    expect(totals.costDocumentsUsd).toBe(1);
    expect(totals.costQuestionsUsd).toBe(0.75);
    expect(totals.costEstimated).toBe(true);
    expect(totals.costPartUnknown).toBe(true);
  });

  it("is all zero and null with no rows", () => {
    const totals = periodTotals([], "2026-10-05", "2026-10-11");
    expect(totals.activeOrganisations).toBe(0);
    expect(totals.costUsd).toBeNull();
  });
});

describe("changeText", () => {
  it("says counts in neutral rounded words", () => {
    expect(changeText(112, 100, "count")).toBe("up 12%");
    expect(changeText(96, 100, "count")).toBe("down 4%");
    expect(changeText(100, 100, "count")).toBe("no change");
    expect(changeText(1004, 1000, "count")).toBe("no change");
    expect(changeText(0, 10, "count")).toBe("down 100%");
    expect(changeText(1300, 100, "count")).toBe("up 1,200%");
  });

  it("guards a 0 base and a tiny base", () => {
    expect(changeText(12, 0, "count")).toBe("new this period");
    expect(changeText(0, 0, "count")).toBe("no change");
    expect(changeText(9, 4, "count")).toBe("too few to compare");
    expect(changeText(9, 5, "count")).toBe("up 80%");
    expect(changeText(4, 4, "count")).toBe("no change");
  });

  it("says money changes on any dollar base, guarding only a 0 before", () => {
    expect(changeText(2.1, 3.0, "money")).toBe("down 30%");
    expect(changeText(0.5, 0.25, "money")).toBe("up 100%");
    expect(changeText(1.2, 0, "money")).toBe("new this period");
    expect(changeText(0, 0, "money")).toBe("no change");
    expect(changeText(3, 3, "money")).toBe("no change");
    expect(changeText(9, 4, "count")).toBe("too few to compare"); // the count guard is unchanged
  });

  it("says shares in points, one decimal", () => {
    expect(changeText(0.312, 0.3, "share")).toBe("up 1.2 points");
    expect(changeText(0.296, 0.3, "share")).toBe("down 0.4 points");
    expect(changeText(0.31, 0.3, "share")).toBe("up 1 point");
    expect(changeText(0.05, 0, "share")).toBe("up 5 points");
    expect(changeText(0.3, 0.3, "share")).toBe("no change");
    expect(changeText(0, 0, "share")).toBe("no change");
  });

  it("never says Infinity or NaN", () => {
    const odd = [0, 1, 4, 5, NaN, Infinity, -Infinity];
    for (const now of odd) {
      for (const before of odd) {
        for (const kind of ["count", "share"] as const) {
          expect(changeText(now, before, kind)).not.toMatch(/Infinity|NaN/);
        }
      }
    }
    expect(changeText(NaN, 5, "count")).toBe("too few to compare");
  });
});

describe("rateText", () => {
  it("says a suppressed rate with its n", () => {
    expect(rateText(null, 8)).toBe("Too few to rate (8)");
    expect(rateText(null, 1200)).toBe("Too few to rate (1,200)");
  });

  it("gives one decimal of the whole, a dash for nothing", () => {
    expect(rateText(1, 8)).toBe("12.5%");
    expect(rateText(0, 10)).toBe("0.0%");
    expect(rateText(10, 10)).toBe("100.0%");
    expect(rateText(0, 0)).toBe("\u2014");
  });
});

describe("unit costs", () => {
  it("is per 1,000 pages and per 100 questions", () => {
    expect(usdPer1000Pages(2, 500)).toBe(4);
    expect(usdPer100Questions(1, 50)).toBe(2);
  });

  it("is null without a cost or with a 0 denominator", () => {
    expect(usdPer1000Pages(null, 500)).toBeNull();
    expect(usdPer1000Pages(2, 0)).toBeNull();
    expect(usdPer100Questions(null, 50)).toBeNull();
    expect(usdPer100Questions(1, 0)).toBeNull();
    expect(usdPer1000Pages(0, 10)).toBe(0);
  });
});

describe("monthForecast", () => {
  const month = (today: string, days_in_month = 31) => ({ from: `${today.slice(0, 8)}01`, today, days_in_month });

  it("draws a straight line to the month end and finds the day it passes the budget", () => {
    expect(monthForecast(50, 100, month("2026-10-10"))).toEqual({ usd: 155, passDay: 20 });
    expect(monthForecast(10, 100, month("2026-10-11")).passDay).toBeNull(); // about $28: never
  });

  it("works on day 1 and on the last day", () => {
    expect(monthForecast(5, 100, month("2026-10-01"))).toEqual({ usd: 155, passDay: 20 });
    expect(monthForecast(90, 100, month("2026-10-31"))).toEqual({ usd: 90, passDay: null });
    expect(monthForecast(30, 100, month("2026-09-30", 30))).toEqual({ usd: 30, passDay: null });
  });

  it("passes on the last day exactly, and not again once already passed", () => {
    expect(monthForecast((100 * 10) / 31, 100, month("2026-10-10")).passDay).toBe(31);
    expect(monthForecast(100, 100, month("2026-10-10")).passDay).toBeNull();
    expect(monthForecast(120, 100, month("2026-10-10")).passDay).toBeNull();
  });

  it("has no pass day with no spend or no budget, and nothing with no cost known", () => {
    expect(monthForecast(0, 100, month("2026-10-10"))).toEqual({ usd: 0, passDay: null });
    expect(monthForecast(50, null, month("2026-10-10"))).toEqual({ usd: 155, passDay: null });
    expect(monthForecast(null, 100, month("2026-10-10"))).toEqual({ usd: null, passDay: null });
  });
});

describe("orgFlags", () => {
  it("has nothing to say about a calm organisation", () => {
    expect(orgFlags(org(), reply(), NOW)).toEqual([]);
  });

  // Changed on purpose (real figures, Tushar 16 Oct 2026): the spend is this month's real
  // cost from the daily rows (month_cost_usd, with estimates, is not used), the calls of
  // unknown cost make it "part not known", and a forecast needs 7 days of figures.
  const spentReply = (usd: number | null, days = 1, unknownCalls = 0) =>
    reply({
      daily: Array.from({ length: days }, (_, i) =>
        day({ day: `2026-10-${String(1 + i).padStart(2, "0")}`, cost_real_usd: usd === null ? null : usd / days, cost_unknown_calls: i === 0 ? unknownCalls : 0 }),
      ),
    });

  it("acts at 100% of the budget and checks at 80%, in the budget's words", () => {
    const passed = orgFlags(org({ ai_budget_usd: 100, month_cost_usd: 1 }), spentReply(100), NOW);
    expect(passed).toEqual([
      { code: "budget_passed", severity: "act", text: "AI spend has reached its budget ($100.00 of $100.00 this month)." },
    ]);
    const near = orgFlags(org({ ai_budget_usd: 100 }), spentReply(99.99, 1, 2), NOW);
    expect(near).toEqual([
      {
        code: "budget_near",
        severity: "check",
        text: "AI spend is at 99% of its budget ($99.99 (part not known) of $100.00 this month).",
      },
    ]);
    expect(codes(org({ ai_budget_usd: 100 }), spentReply(80))).toEqual(["budget_near"]);
    // 79.99 by day 11 is on course for about $225: the forecast flag, not "near", with 7 days of figures.
    expect(codes(org({ ai_budget_usd: 100 }), spentReply(79.99, 7))).toEqual(["forecast_passes"]);
    expect(codes(org({ ai_budget_usd: 100 }), spentReply(79.99, 6))).toEqual([]);
  });

  it("checks a forecast that passes the budget this month, and not one that does not", () => {
    const [flag] = orgFlags(org({ ai_budget_usd: 100 }), spentReply(50, 7), NOW);
    expect(flag).toEqual({
      code: "forecast_passes",
      severity: "check",
      text: "AI spend is on course to pass its budget around day 22 of the month (about $140.91 by month end; $50.00 of $100.00 this month).",
    });
    expect(codes(org({ ai_budget_usd: 1000 }), spentReply(50, 7))).toEqual([]);
  });

  it("has no budget flag with no budget, no cost known or no spend", () => {
    expect(codes(org({ ai_budget_usd: null }), spentReply(500))).toEqual([]);
    expect(codes(org({ ai_budget_usd: 100, month_cost_usd: 500 }), spentReply(null))).toEqual([]);
    expect(codes(org({ ai_budget_usd: 0 }), spentReply(0))).toEqual([]);
  });

  it("checks 14 quiet days from the last active day, not 13, and never for an organisation never used", () => {
    expect(codes(org({ last_active_day: "2026-09-27" }), reply30([]))).toEqual(["quiet"]);
    expect(orgFlags(org({ last_active_day: "2026-09-27" }), reply30([]), NOW)[0]?.text).toBe(
      "No uploads, questions or reading for 14 days.",
    );
    expect(codes(org({ last_active_day: "2026-09-28" }), reply30([]))).toEqual([]);
    expect(codes(org({ last_active_day: null }), reply30([]))).toEqual([]);
    expect(codes(org({ last_active_day: "not a date" }), reply30([]))).toEqual([]);
  });

  it("says quiet on the 7-day period too, from a last active day before the reply's days", () => {
    // The 7-day reply covers 28 Sep to 11 Oct only; the last use was 1 Sep.
    expect(orgFlags(org({ last_active_day: "2026-09-01" }), reply(), NOW)).toEqual([
      { code: "quiet", severity: "check", text: "No uploads, questions or reading for 40 days." },
    ]);
  });

  it("only looks at its own organisation's days", () => {
    expect(codes(org(), reply30([day({ organisation_id: "b", day: "2026-09-01", uploads: 20 })]))).toEqual([]);
  });

  it("checks uploads and questions down by half on 10 or more before", () => {
    const before = day({ day: "2026-09-30", uploads: 6, questions: 4 });
    const flags = orgFlags(org(), reply({ daily: [before, day({ day: "2026-10-10", uploads: 5 })] }), NOW);
    expect(flags).toEqual([
      { code: "activity_down", severity: "check", text: "Uploads and questions are down 50% on the period before (5 against 10)." },
    ]);
    expect(codes(org(), reply({ daily: [before, day({ day: "2026-10-10", uploads: 6 })] }))).toEqual([]);
    expect(codes(org(), reply({ daily: [day({ day: "2026-09-30", uploads: 9 }), day({ day: "2026-10-10", uploads: 0, runs: 1 })] }))).toEqual([]);
  });

  it("does not say activity is down when it says quiet", () => {
    const daily = [day({ day: "2026-08-20", uploads: 50 }), day({ day: "2026-09-20", uploads: 1 })];
    expect(codes(org({ last_active_day: "2026-09-20" }), reply30(daily))).toEqual(["quiet"]);
    expect(codes(org({ last_active_day: "2026-10-01" }), reply30(daily))).toEqual(["activity_down"]);
  });

  it("checks not found at 30% of answers, not 29%, and never a suppressed share", () => {
    const [flag] = orgFlags(org({ answers_30d: 100, not_found_30d: 30 }), reply(), NOW);
    expect(flag).toEqual({
      code: "not_found_high",
      severity: "check",
      text: "30% of answers in the last 30 days found nothing (30 of 100).",
    });
    expect(codes(org({ answers_30d: 100, not_found_30d: 29 }))).toEqual([]);
    expect(codes(org({ answers_30d: 9, not_found_30d: null }))).toEqual([]);
  });

  it("checks a review item older than 7 days, not one exactly 7 days old", () => {
    const seven = new Date(NOW.getTime() - 7 * 86_400_000).toISOString();
    const older = new Date(NOW.getTime() - 7 * 86_400_000 - 60_000).toISOString();
    expect(codes(org({ open_review: 3, oldest_open_review_at: seven }))).toEqual([]);
    expect(orgFlags(org({ open_review: 3, oldest_open_review_at: older }), reply(), NOW)).toEqual([
      { code: "review_old", severity: "check", text: "The oldest of 3 items waiting for review is 7 days old." },
    ]);
    expect(codes(org({ open_review: 1, oldest_open_review_at: "not a date" }))).toEqual([]);
  });

  // Changed on purpose (real figures, Tushar 16 Oct 2026): a spike is in the real cost.
  const week = (cost: number | null) =>
    dayList("2026-10-04", "2026-10-10").map((d) => day({ day: d, cost_real_usd: cost }));

  it("checks spend today over twice the median of the 7 days before", () => {
    expect(orgFlags(org(), reply({ daily: [...week(1), day({ cost_real_usd: 2.01, cost_usd: 1 })] }), NOW)).toEqual([
      { code: "spend_spike", severity: "check", text: "AI spend today ($2.01) is more than twice a usual day ($1.00)." },
    ]);
    expect(codes(org(), reply({ daily: [...week(1), day({ cost_real_usd: 2, cost_usd: 9 })] }))).toEqual([]);
  });

  it("does not call spend under $1 a spike, even from nothing", () => {
    expect(codes(org(), reply({ daily: [day({ cost_real_usd: 1 })] }))).toEqual([]);
    expect(codes(org(), reply({ daily: [...week(null), day({ cost_real_usd: 1.01 })] }))).toEqual(["spend_spike"]);
  });

  it("uses the median, so one big day before does not hide a spike", () => {
    const daily = [...week(0.5).slice(1), day({ day: "2026-10-04", cost_real_usd: 50 }), day({ cost_real_usd: 1.5 })];
    expect(codes(org(), reply({ daily }))).toEqual(["spend_spike"]);
  });
});

describe("attentionItems", () => {
  const names = new Map([["a", "Acme Traders"]]);

  it("puts each organisation's flags under its name, with Act now first", () => {
    const r = reply({
      organisations: [
        org({ answers_30d: 100, not_found_30d: 40 }),
        org({ organisation_id: "gone", ai_budget_usd: 10 }),
      ],
      daily: [day({ organisation_id: "gone", day: "2026-10-02", cost_real_usd: 12 })], // real figures (16 Oct 2026)
    });
    expect(attentionItems(r, names, quality({}), health(), NOW)).toEqual([
      {
        severity: "act",
        text: "An organisation: AI spend has reached its budget ($12.00 of $10.00 this month).",
        organisationId: "gone",
        organisationName: null,
        target: "organisation",
      },
      {
        severity: "check",
        text: "Acme Traders: 40% of answers in the last 30 days found nothing (40 of 100).",
        organisationId: "a",
        organisationName: "Acme Traders",
        target: "organisation",
      },
    ]);
  });

  it("acts on platform-caused failed runs at 10% of K or more runs", () => {
    const items = attentionItems(reply(), names, quality({ runs: 10, runs_failed_platform: 1 }), health(), NOW);
    expect(items).toEqual([
      {
        severity: "act",
        text: "10% of reading runs failed for platform reasons (1 of 10).",
        organisationId: null,
        organisationName: null,
        target: "health",
      },
    ]);
    expect(attentionItems(reply(), names, quality({ runs: 100, runs_failed_platform: 9 }), health(), NOW)).toEqual([]);
    expect(attentionItems(reply(), names, quality({ runs: 9, runs_failed_platform: 9 }), health(), NOW)).toEqual([]);
  });

  it("checks runs ready without a card at 5%, pointing at Models", () => {
    const [item] = attentionItems(reply(), names, quality({ runs: 100, runs_degraded: 5 }), health(), NOW);
    expect(item).toMatchObject({ severity: "check", target: "models" });
    expect(item?.text).toBe("5% of reading runs finished without the AI card (5 of 100).");
    expect(attentionItems(reply(), names, quality({ runs: 100, runs_degraded: 4 }), health(), NOW)).toEqual([]);
  });

  it("checks the oldest document in progress over 30 minutes", () => {
    const waited = (seconds: number) =>
      health({ totals: { documents_by_status: {}, failed_by_code: {}, in_progress: 1, oldest_in_progress_seconds: seconds } });
    const [item] = attentionItems(reply(), names, quality({}), waited(30 * 60 + 1), NOW);
    expect(item).toMatchObject({ severity: "check", target: "health", text: "The oldest document in progress has waited 30 minutes." });
    expect(attentionItems(reply(), names, quality({}), waited(30 * 60), NOW)).toEqual([]);
  });

  it("acts on a fresh reader report of down or key problem, not a stale one", () => {
    const seen = (ms: number) => new Date(NOW.getTime() - ms).toISOString();
    const h = health({
      reader: [
        { worker: "w1", state: "down", since: null, seen_at: seen(1000) },
        { worker: "w2", state: "key_problem", since: null, seen_at: seen(1000) },
        { worker: "w3", state: "key_problem", since: null, seen_at: seen(1000) },
        { worker: "w4", state: "down", since: null, seen_at: seen(3 * 60 * 1000) },
        { worker: "w5", state: "failing", since: null, seen_at: seen(1000) },
        { worker: "w6", state: "working", since: null, seen_at: seen(1000) },
      ],
    });
    expect(attentionItems(reply(), names, quality({}), h, NOW).map((i) => [i.severity, i.target, i.text])).toEqual([
      ["act", "health", "The scan reader is down on 1 worker."],
      // Changed on purpose (issue 407): a wrong reading model (OCR_MODEL) is reported as key_problem too.
      ["act", "health", "The scan reader's key or model setting needs fixing on 2 workers."],
    ]);
    expect(attentionItems(reply(), names, quality({}), health({ reader: undefined, queues: null }), NOW)).toEqual([]);
  });

  it("keeps platform items before organisations within each severity", () => {
    const items = attentionItems(
      reply({
        organisations: [org({ ai_budget_usd: 10 }), org({ organisation_id: "b", answers_30d: 10, not_found_30d: 5 })],
        daily: [day({ day: "2026-10-02", cost_real_usd: 10 })], // real figures (16 Oct 2026)
      }),
      names,
      quality({ runs: 100, runs_failed_platform: 50, runs_degraded: 50 }),
      health(),
      NOW,
    );
    expect(items.map((i) => [i.severity, i.organisationId])).toEqual([
      ["act", null],
      ["act", "a"],
      ["check", null],
      ["check", "b"],
    ]);
  });

  it("never gives a false all-clear when quality or health could not be loaded", () => {
    const incomplete = {
      severity: "check",
      text: "Health or quality figures could not be loaded, so this list may be incomplete.",
      organisationId: null,
      organisationName: null,
      target: "health",
    };
    expect(attentionItems(reply(), names, null, health(), NOW)).toEqual([incomplete]);
    expect(attentionItems(reply(), names, quality({}), null, NOW)).toEqual([incomplete]);
    expect(attentionItems(reply(), names, null, null, NOW)).toEqual([incomplete]);
    expect(attentionItems(reply(), names, quality({}), health(), NOW)).toEqual([]);
  });

  it("reads no platform row (null) as nothing to say, not a crash", () => {
    const noPlatform: QualityReply = { ...quality({}), platform: null };
    expect(attentionItems(reply(), names, noPlatform, health(), NOW)).toEqual([]);
  });
});

describe("riskOrder", () => {
  it("puts act first and keeps each group's order", () => {
    const items = [
      { severity: "check" as const, id: 1 },
      { severity: "act" as const, id: 2 },
      { severity: "check" as const, id: 3 },
      { severity: "act" as const, id: 4 },
    ];
    expect(riskOrder(items).map((i) => i.id)).toEqual([2, 4, 1, 3]);
    expect(items.map((i) => i.id)).toEqual([1, 2, 3, 4]); // a copy, the input untouched
    expect(riskOrder([])).toEqual([]);
  });
});

describe("csvOf", () => {
  it("ends every row with CRLF and leaves null empty", () => {
    expect(csvOf([["Organisation", "Pages"], ["Acme", 12], ["Beta", null]])).toBe("Organisation,Pages\r\nAcme,12\r\nBeta,\r\n");
    expect(csvOf([])).toBe("");
  });

  it("quotes a comma, a quote or a newline, doubling quotes", () => {
    expect(csvOf([["a,b", 'say "hi"', "line\nbreak", "cr\rx"]])).toBe('"a,b","say ""hi""","line\nbreak","cr\rx"\r\n');
  });

  it("guards a text cell that starts like a formula, as the backend's csv_cell", () => {
    expect(csvOf([["=SUM(A1)", "+1", "-x", "@x", "\tx"]])).toBe("'=SUM(A1),'+1,'-x,'@x,'\tx\r\n");
    expect(csvOf([["\rx", "=a,b"]])).toBe("\"'\rx\",\"'=a,b\"\r\n");
    expect(csvOf([["a=b", "x-1"]])).toBe("a=b,x-1\r\n");
  });

  it("writes numbers as numbers, a non-finite one as empty", () => {
    expect(csvOf([[1.5, -2, 0, NaN, Infinity]])).toBe("1.5,-2,0,,\r\n");
  });
});

describe("boardRows", () => {
  it("says too few under 30 documents, not at 30", () => {
    const [few, enough] = boardRows([board({ model: "a", documents: 29 }), board({ model: "b", documents: 30 })]);
    expect(few?.tooFew).toBe(true);
    expect(enough?.tooFew).toBe(false);
  });

  it("adds AI, local and cached pages, and prices the billed AI pages only", () => {
    const [line] = boardRows([
      board({ pages_ai: 1000, pages_local: 200, pages_cached: 300, scan_cost_usd: 3, cost_usd: 5, scan_calls: 40, cached_scan_calls: 10 }),
    ]);
    expect(line?.pages).toBe(1500);
    expect(line?.usdPer1000Pages).toBe(3);
    expect(line?.cachedShare).toBe(0.25);
  });

  it("has no price without AI pages or a cost, and no cache share without calls", () => {
    const [local, keyless] = boardRows([
      board({ model: "a", pages_local: 50, scan_cost_usd: 1 }),
      board({ model: "rapidocr", pages_local: 10, scan_cost_usd: null }),
    ]);
    expect(local?.usdPer1000Pages).toBeNull();
    expect(keyless?.usdPer1000Pages).toBeNull();
    expect(keyless?.cachedShare).toBeNull();
  });

  it("orders most pages first, then by model", () => {
    const lines = boardRows([
      board({ model: "c", pages_ai: 5 }),
      board({ model: "b", pages_ai: 10 }),
      board({ model: "a", pages_local: 5 }),
    ]);
    expect(lines.map((l) => l.row.model)).toEqual(["b", "a", "c"]);
  });
});

describe("boardBest", () => {
  const reads = { pages_ai: 1000, runs: 40, scan_cost_usd: 2, cost_usd: 5, seconds_per_page_p50: 4 };
  // Same pages, so boardRows keeps them in model order: m0, m1, ...
  const best = (...rows: Partial<BoardRow>[]) => boardBest(boardRows(rows.map((row, i) => board({ model: `m${i}`, ...reads, ...row }))));

  it("marks the lowest cost, the fastest speed and the fewest failed in each column", () => {
    const lines = boardRows([
      board({ model: "a", ...reads, scan_cost_usd: 1, seconds_per_page_p50: 6, runs_failed: 2, cost_usd: 9 }),
      board({ model: "b", ...reads, scan_cost_usd: 3, seconds_per_page_p50: 2, runs_failed: 0, runs_degraded: 1, cost_usd: 4 }),
    ]);
    const byModel = new Map(lines.map((line, i) => [line.row.model, boardBest(lines)[i]]));
    expect(byModel.get("a")).toEqual({ per1000: true, seconds: false, failed: false, cost: false });
    expect(byModel.get("b")).toEqual({ per1000: false, seconds: true, failed: true, cost: true });
  });

  it("tags every tied model, rounded as the table shows the value", () => {
    const [a, b] = best({ seconds_per_page_p50: 4.21, cost_usd: 5 }, { seconds_per_page_p50: 4.24, cost_usd: 5.001 });
    expect([a?.seconds, b?.seconds]).toEqual([true, true]);
    expect([a?.cost, b?.cost]).toEqual([true, true]);
    expect([a?.per1000, b?.per1000, a?.failed, b?.failed]).toEqual([true, true, true, true]);
  });

  it("never tags a missing value, and tags nothing unless two models have one", () => {
    const tags = boardBest(
      boardRows([
        board({ model: "a", ...reads, seconds_per_page_p50: null, cost_usd: null }),
        board({ model: "b", ...reads, seconds_per_page_p50: null, cost_usd: 1, cost_part_unknown: true }),
        board({ model: "c", ...reads, seconds_per_page_p50: 2, cost_usd: 8 }),
      ]),
    );
    expect(tags.some((t) => t.seconds)).toBe(false); // only c has a speed: nothing to beat
    expect(tags.some((t) => t.cost)).toBe(false); // part not known is a floor; c alone is left
  });

  it("tags nothing for one model, and skips a model too few to compare or that reads no pages", () => {
    expect(best({})).toEqual([{ per1000: false, seconds: false, failed: false, cost: false }]);
    const tags = boardBest(
      boardRows([
        board({ model: "few", ...reads, documents: 29, scan_cost_usd: 0.5, seconds_per_page_p50: 1 }),
        board({ model: "chat", runs: 0, cost_usd: 0.1 }),
        board({ model: "x", ...reads, scan_cost_usd: 3, seconds_per_page_p50: 5 }),
      ]),
    );
    expect(tags.some((t) => t.per1000 || t.seconds || t.failed)).toBe(false);
    // AI cost is shown on every row, so every model with a known cost is ranked.
    expect(tags.filter((t) => t.cost)).toHaveLength(1);
  });
});

describe("Sparkline", () => {
  const draw = (values: number[]) => renderToStaticMarkup(createElement(Sparkline, { values }));
  const points = (html: string) => /points="([^"]*)"/.exec(html)?.[1];

  it("is a hidden, unfocusable SVG polyline that scales without thickening", () => {
    const html = draw([1, 2, 3]);
    expect(html).toContain('viewBox="0 0 100 24"');
    expect(html).toContain('preserveAspectRatio="none"');
    expect(html).toContain('aria-hidden="true"');
    expect(html).toContain('focusable="false"');
    expect(html).toContain('vector-effect="non-scaling-stroke"');
    expect(html).toContain('stroke="currentColor"');
    expect(html).toContain('fill="none"');
    expect(html).not.toMatch(/<canvas|role="img"|#[0-9a-f]{3,6}\b|rgb\(/i);
  });

  it("starts y at 0, not at the smallest value", () => {
    expect(points(draw([0, 10]))).toBe("0,23 100,1");
    expect(points(draw([5, 10, 10]))).toBe("0,12 50,1 100,1");
  });

  it("draws a flat baseline for nothing, all zero or bad values, and a level line for one point", () => {
    expect(points(draw([]))).toBe("0,23 100,23");
    expect(points(draw([0, 0, 0]))).toBe("0,23 50,23 100,23");
    expect(points(draw([NaN, -3]))).toBe("0,23 100,23");
    expect(points(draw([7]))).toBe("0,1 100,1");
  });
});

describe("ShareBar", () => {
  const draw = (parts: SharePart[]) => renderToStaticMarkup(createElement(ShareBar, { parts }));

  it("draws hidden CSS segments and says each share in a text legend", () => {
    const html = draw([
      { label: "Cells", value: 71 },
      { label: "Scanned", value: 29 },
      { label: "Blank", value: 0 },
    ]);
    const bars = html.match(/<span style="width:[^"]*"/g) ?? [];
    expect(bars).toHaveLength(2);
    expect(bars[0]).toContain("width:71%");
    expect(bars[1]).toContain("width:29%");
    expect(html).toMatch(/<div aria-hidden="true"[^>]*><span style="width:71%/);
    expect(html).toContain("Cells 71%");
    expect(html).toContain("Scanned 29%");
    expect(html).toContain("Blank 0%");
    expect(html).not.toMatch(/<canvas|role="img"|#[0-9a-f]{3,6}\b|rgb\(/i);
    expect(html).toContain("var(--accent)");
  });

  it("says a tiny share as under 1%, never 0%", () => {
    const html = draw([
      { label: "Text layer", value: 120000 },
      { label: "Unreadable", value: 1 },
    ]);
    expect(html).toContain("Text layer 100%");
    expect(html).toContain("Unreadable &lt; 1%");
  });

  it("says there is nothing to show instead of an empty bar", () => {
    for (const parts of [[], [{ label: "Cells", value: 0 }], [{ label: "Odd", value: NaN }]]) {
      const html = draw(parts);
      expect(html).toBe("<p>Nothing in this period.</p>");
    }
  });
});
