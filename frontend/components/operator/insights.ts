// The operator's Overview, Quality and Compare models (operator insights, council 11 Oct
// 2026, F1-F4): days, totals, change and rate words, unit costs, the month-end forecast,
// "Needs attention", the CSV and the model board's lines. Kept pure, so vitest can prove
// them. The server sends counts, sums and codes only; a null is a value it suppressed
// below K (or a cost nobody knows), never a zero.

import { budgetLine } from "@/components/admin/logic";
import { formatNumber, formatUsd } from "@/lib/format";
import type { BoardRow, InsightDay, InsightsReply, OrgInsight, PlatformHealth, QualityReply } from "@/lib/types";
import { NOT_REPORTING_MS, waitWords } from "./platform";

export const INSIGHT_PERIODS = [7, 30, 90] as const;
export type InsightPeriod = (typeof INSIGHT_PERIODS)[number];
/** The database's K: an outcome value over fewer cases is null (PROVISIONAL, decisions Q3). */
export const K_MIN = 10;
/** A model read for fewer documents is "too few" on the board (PROVISIONAL, decisions F4). */
export const BOARD_MIN_DOCUMENTS = 30;
/** PROVISIONAL thresholds (decisions "Needs attention"). */
export const ATTENTION = {
  budgetCheck: 0.8, budgetAct: 1, quietDays: 14, activityDrop: 0.5, activityMinBase: 10,
  notFoundShare: 0.3, failedPlatformShare: 0.1, degradedShare: 0.05, reviewOldDays: 7,
  spikeTimesMedian: 2, spikeFloorUsd: 1, queueOldMinutes: 30,
} as const;
/** A count's change, or a unit cost's on this few pages or questions before, is "too few to compare" (PROVISIONAL, UI/UX F2). */
export const CHANGE_MIN_BASE = 5;
/** Tushar, 16 Oct 2026: a unit cost needs this many pages or questions behind it; fewer say
 * what was spent on them so far, never a per-1,000 figure stretched from one page. */
export const UNIT_MIN_PAGES = 20;
export const UNIT_MIN_QUESTIONS = 10;
/** A month's forecast needs this many days of this month with figures. */
export const FORECAST_MIN_DAYS = 7;
/** A tile's line needs this many days with a value, else it is not drawn. */
export const SPARK_MIN_DAYS = 3;

const DAY_MS = 86_400_000;
const DASH = "\u2014"; // the repo's empty-cell dash

export type DayMetric = "uploads" | "sign_ins" | "questions" | "corrections" | "runs" | "runs_failed"
  | "pages" | "scan_pages" | "cached_pages" | "cost_usd" | "cost_real_usd";

/** A YYYY-MM-DD as days since 1970 (UTC on the string, so no timezone drift); NaN if not a date. */
const dayNumber = (day: string) => Date.parse(`${day}T00:00:00Z`) / DAY_MS;
const dayText = (n: number) => new Date(n * DAY_MS).toISOString().slice(0, 10);

/** Every day from `from` to `to`, both included, across month and year ends; [] when to < from. */
export function dayList(from: string, to: string): string[] {
  const first = dayNumber(from);
  const last = dayNumber(to);
  const days: string[] = [];
  for (let n = first; n <= last; n++) days.push(dayText(n)); // NaN never loops
  return days;
}

/** F1 is sparse: one value per day of `days`, 0 on a day without a row; the platform's sum without an organisation. */
export function series(daily: readonly InsightDay[], days: readonly string[], metric: DayMetric, organisationId?: string): number[] {
  const byDay = new Map<string, number>();
  for (const row of daily) {
    if (organisationId !== undefined && row.organisation_id !== organisationId) continue;
    byDay.set(row.day, (byDay.get(row.day) ?? 0) + (row[metric] ?? 0));
  }
  return days.map((day) => byDay.get(day) ?? 0);
}

export interface PeriodTotals {
  activeOrganisations: number; uploads: number; pages: number; questions: number; runs: number; runsFailed: number;
  scanPages: number; cachedPages: number; costUsd: number | null; costDocumentsUsd: number | null;
  costQuestionsUsd: number | null; costEstimated: boolean; costPartUnknown: boolean;
  // 0034: from the ledger, the real figures (the cost OpenRouter reported only).
  pagesRead: number; pagesReread: number; pagesReadCosted: number; costRealUsd: number | null;
  costScanRealUsd: number | null; costAnswerRealUsd: number | null; unknownCalls: number;
  scanUnknownCalls: number; answerUnknownCalls: number;
}

/** Active: an upload, a question or a run that day (sign-ins alone are not use). */
const isActive = (row: InsightDay) => row.uploads > 0 || row.questions > 0 || row.runs > 0 || row.pages_read > 0;
const addCost = (sum: number | null, cost: number | null) => (cost === null ? sum : (sum ?? 0) + cost);

/** F1 summed over [from, to]; a cost is null when no day in it has a known cost. */
export function periodTotals(daily: readonly InsightDay[], from: string, to: string): PeriodTotals {
  const active = new Set<string>();
  const totals: PeriodTotals = {
    activeOrganisations: 0, uploads: 0, pages: 0, questions: 0, runs: 0, runsFailed: 0, scanPages: 0, cachedPages: 0,
    costUsd: null, costDocumentsUsd: null, costQuestionsUsd: null, costEstimated: false, costPartUnknown: false,
    pagesRead: 0, pagesReread: 0, pagesReadCosted: 0, costRealUsd: null, costScanRealUsd: null, costAnswerRealUsd: null,
    unknownCalls: 0, scanUnknownCalls: 0, answerUnknownCalls: 0,
  };
  for (const row of daily) {
    if (row.day < from || row.day > to) continue;
    if (isActive(row)) active.add(row.organisation_id);
    totals.uploads += row.uploads;
    totals.pages += row.pages;
    totals.questions += row.questions;
    totals.runs += row.runs;
    totals.runsFailed += row.runs_failed;
    totals.scanPages += row.scan_pages;
    totals.cachedPages += row.cached_pages;
    totals.costUsd = addCost(totals.costUsd, row.cost_usd);
    totals.costDocumentsUsd = addCost(totals.costDocumentsUsd, row.cost_documents_usd);
    totals.costQuestionsUsd = addCost(totals.costQuestionsUsd, row.cost_questions_usd);
    totals.costEstimated ||= row.cost_estimated;
    totals.costPartUnknown ||= row.cost_part_unknown;
    totals.pagesRead += row.pages_read;
    totals.pagesReread += row.pages_reread;
    totals.pagesReadCosted += row.pages_read_costed;
    totals.costRealUsd = addCost(totals.costRealUsd, row.cost_real_usd);
    totals.costScanRealUsd = addCost(totals.costScanRealUsd, row.cost_scan_real_usd);
    totals.costAnswerRealUsd = addCost(totals.costAnswerRealUsd, row.cost_answer_real_usd);
    totals.unknownCalls += row.cost_unknown_calls;
    totals.scanUnknownCalls += row.scan_unknown_calls;
    totals.answerUnknownCalls += row.answer_unknown_calls;
  }
  totals.activeOrganisations = active.size;
  return totals;
}

export type ChangeKind = "count" | "share" | "money";

const points = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

/**
 * UI/UX F2: the change against the period before, in neutral words (never colour only).
 * count: "up 12%", "down 4%", "no change"; "new this period" from 0; "too few to compare"
 * when before is under 5. money (US dollars): as count, but only a 0 before is guarded ($3
 * to $2.10 is "down 30%"; a unit cost's caller judges its own pages or questions before).
 * share (0..1): "up 1.2 points", "down 0.4 points"; a share from 0 is points too (0% is a
 * real share; the server already nulls one below K).
 */
export function changeText(now: number, before: number, kind: ChangeKind): string {
  if (!Number.isFinite(now) || !Number.isFinite(before)) return "too few to compare";
  if (kind === "share") {
    const change = Math.round((now - before) * 1000) / 10;
    if (change === 0) return "no change";
    const size = Math.abs(change);
    return `${change > 0 ? "up" : "down"} ${points.format(size)} ${size === 1 ? "point" : "points"}`;
  }
  if (now === before) return "no change";
  if (before === 0) return now > 0 ? "new this period" : "too few to compare";
  if (kind === "count" && before < CHANGE_MIN_BASE) return "too few to compare";
  const percent = Math.round(((now - before) / before) * 100);
  if (percent === 0) return "no change";
  return `${percent > 0 ? "up" : "down"} ${formatNumber(Math.abs(percent))}%`;
}

/**
 * A tile's change line, in plain words (Tushar, 16 Oct 2026): "Up 12% on the previous 30
 * days", "Same as the previous 30 days", "None in the previous 30 days" (from 0), "Not
 * enough earlier data to compare"; null when both are 0 (nothing to say).
 */
export function changeLine(now: number, before: number, kind: ChangeKind, days: number): string | null {
  const text = changeText(now, before, kind);
  const previous = `the previous ${formatNumber(days)} days`;
  if (text === "no change") return now === 0 ? null : `Same as ${previous}`;
  if (text === "new this period") return `None in ${previous}`;
  if (text === "too few to compare") return "Not enough earlier data to compare";
  return `${text.charAt(0).toUpperCase()}${text.slice(1)} on ${previous}`;
}

/** A day series to draw, or undefined when fewer than SPARK_MIN_DAYS days have a value (no made-up line). */
export const sparkOf = (values: readonly number[]) =>
  values.filter((value) => Number.isFinite(value) && value > 0).length >= SPARK_MIN_DAYS ? [...values] : undefined;

/** "cost not known for 12 calls"; null for none. */
export const unknownText = (calls: number) =>
  calls > 0 ? `cost not known for ${formatNumber(calls)} ${calls === 1 ? "call" : "calls"}` : null;

/**
 * This month's real spend (the cost OpenRouter reported) to a straight-line month end, from
 * the daily rows; null unless they cover the whole month so far and at least
 * FORECAST_MIN_DAYS days of it have figures.
 */
export function realMonthForecast(reply: InsightsReply): number | null {
  const { month } = reply;
  if (reply.previous_from > month.from) return null;
  const rows = reply.daily.filter((row) => row.day >= month.from && row.day <= month.today && row.cost_real_usd !== null);
  if (new Set(rows.map((row) => row.day)).size < FORECAST_MIN_DAYS) return null;
  return monthForecast(sum(rows.map((row) => row.cost_real_usd ?? 0)), null, month).usd;
}

/** "12.5%" of the whole; "Too few to rate (n)" when the server suppressed it; a dash for nothing. */
export function rateText(part: number | null, whole: number): string {
  if (part === null) return `Too few to rate (${formatNumber(whole)})`;
  if (whole <= 0) return DASH;
  return `${((part / whole) * 100).toFixed(1)}%`;
}

const perUnit = (usd: number | null, count: number, unit: number) => (usd === null || count <= 0 ? null : (usd / count) * unit);

/** UI/UX F1: US dollars per 1,000 pages; null without a cost or pages. */
export const usdPer1000Pages = (usd: number | null, pages: number) => perUnit(usd, pages, 1000);
/** UI/UX F1: US dollars per 100 questions; null without a cost or questions. */
export const usdPer100Questions = (usd: number | null, questions: number) => perUnit(usd, questions, 100);

export interface MonthForecast { usd: number | null; passDay: number | null }

/**
 * A straight line: spent / today's day number * days in the month. passDay is the first
 * day of this month the line reaches the budget, when that is still ahead (after today);
 * null when it has already passed (budget_passed says so), never does, or no budget.
 */
export function monthForecast(spent: number | null, budget: number | null, month: InsightsReply["month"]): MonthForecast {
  const elapsed = Number(month.today.slice(8, 10));
  if (spent === null || !(elapsed > 0)) return { usd: null, passDay: null };
  const usd = (spent / elapsed) * month.days_in_month;
  if (budget === null || spent <= 0) return { usd, passDay: null };
  const day = Math.ceil((budget * elapsed) / spent - 1e-9); // the float slack keeps an exact day exact
  return { usd, passDay: day > elapsed && day <= month.days_in_month ? day : null };
}

export type Severity = "act" | "check";
export type FlagCode = "budget_passed" | "budget_near" | "forecast_passes" | "quiet" | "activity_down"
  | "not_found_high" | "review_old" | "spend_spike";
export interface Flag { code: FlagCode; severity: Severity; text: string }

const percentOf = (part: number, whole: number) => `${formatNumber(Math.floor((part / whole) * 100))}%`;
const sum = (values: readonly number[]) => values.reduce((a, b) => a + b, 0);

function median(values: readonly number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid]! : ((sorted[mid - 1] ?? 0) + (sorted[mid] ?? 0)) / 2;
}

/**
 * One organisation's "Needs attention" flags (decisions, PROVISIONAL thresholds), from F2
 * and F1. One budget flag at most: passed (act), else near 80% (check), else the forecast
 * passes it this month (check). Quiet: 14 days or more from the last active day (F2's
 * last_active_day, any time before) to the reply's last day; never active is not quiet;
 * quiet hides "activity down". Spend spike: today over 2 x the median of the 7 days before and
 * over $1. Review: the oldest open item more than 7 days old at `now`.
 */
export function orgFlags(org: OrgInsight, reply: InsightsReply, now: Date): Flag[] {
  const flags: Flag[] = [];
  const id = org.organisation_id;
  const daily = reply.daily.filter((row) => row.organisation_id === id);
  const budget = org.ai_budget_usd;
  const spent = org.month_cost_usd;
  if (budget !== null && spent !== null) {
    const line = budgetLine({ ai_budget_usd: budget, spent_this_month_usd: spent, spent_part_unknown: org.month_cost_part_unknown });
    const forecast = monthForecast(spent, budget, reply.month);
    if (spent > 0 && spent >= budget * ATTENTION.budgetAct) {
      flags.push({ code: "budget_passed", severity: "act", text: `AI spend has reached its budget (${line}).` });
    } else if (spent > 0 && spent >= budget * ATTENTION.budgetCheck) {
      flags.push({ code: "budget_near", severity: "check", text: `AI spend is at ${percentOf(spent, budget)} of its budget (${line}).` });
    } else if (forecast.passDay !== null && forecast.usd !== null) {
      flags.push({
        code: "forecast_passes",
        severity: "check",
        text: `AI spend is on course to pass its budget around day ${forecast.passDay} of the month (about ${formatUsd(forecast.usd)} by month end; ${line}).`,
      });
    }
  }

  const quietDays = org.last_active_day === null ? 0 : dayNumber(reply.to) - dayNumber(org.last_active_day);
  if (quietDays >= ATTENTION.quietDays) {
    flags.push({ code: "quiet", severity: "check", text: `No uploads, questions or reading for ${formatNumber(quietDays)} days.` });
  } else {
    const work = (from: string, to: string) =>
      sum(daily.filter((row) => row.day >= from && row.day <= to).map((row) => row.uploads + row.questions));
    const before = work(reply.previous_from, dayText(dayNumber(reply.from) - 1));
    const current = work(reply.from, reply.to);
    if (before >= ATTENTION.activityMinBase && current <= before * (1 - ATTENTION.activityDrop)) {
      flags.push({
        code: "activity_down",
        severity: "check",
        text: `Uploads and questions are ${changeText(current, before, "count")} on the period before (${formatNumber(current)} against ${formatNumber(before)}).`,
      });
    }
  }

  if (org.not_found_30d !== null && org.answers_30d > 0 && org.not_found_30d / org.answers_30d >= ATTENTION.notFoundShare) {
    flags.push({
      code: "not_found_high",
      severity: "check",
      text: `${percentOf(org.not_found_30d, org.answers_30d)} of answers in the last 30 days found nothing (${formatNumber(org.not_found_30d)} of ${formatNumber(org.answers_30d)}).`,
    });
  }

  const oldest = org.oldest_open_review_at === null ? NaN : Date.parse(org.oldest_open_review_at);
  if (now.getTime() - oldest > ATTENTION.reviewOldDays * DAY_MS) {
    const days = Math.floor((now.getTime() - oldest) / DAY_MS);
    flags.push({
      code: "review_old",
      severity: "check",
      text: `The oldest of ${formatNumber(org.open_review)} items waiting for review is ${formatNumber(days)} days old.`,
    });
  }

  const today = dayNumber(reply.to);
  const costs = series(daily, dayList(dayText(today - 7), reply.to), "cost_usd");
  const spike = costs.pop() ?? 0;
  const usual = median(costs);
  if (spike > ATTENTION.spikeFloorUsd && spike > usual * ATTENTION.spikeTimesMedian) {
    flags.push({
      code: "spend_spike",
      severity: "check",
      text: `AI spend today (${formatUsd(spike)}) is more than twice a usual day (${formatUsd(usual)}).`,
    });
  }
  return flags;
}

export interface AttentionItem {
  severity: Severity; text: string; organisationId: string | null; organisationName: string | null;
  target: "organisation" | "health" | "models";
}

const share = (part: number, whole: number) => (whole >= K_MIN ? part / whole : 0);

/**
 * "Needs attention" for the whole platform, riskiest first: the platform's own items
 * (F3 platform row: runs failed for platform reasons >= 10% act, ready without a card
 * >= 5% check, both only over K runs; /operator/health: a fresh reader report "down" or
 * "key_problem" act, the oldest document in progress over 30 minutes check), then each
 * organisation's flags under its name from `names` (listOrgs). Quality or health not
 * loaded (null) adds a check that the list may be incomplete, so it is never a false all-clear.
 */
export function attentionItems(reply: InsightsReply, names: ReadonlyMap<string, string>,
  quality: QualityReply | null, health: PlatformHealth | null, now: Date): AttentionItem[] {
  const items: AttentionItem[] = [];
  const platform = (severity: Severity, text: string, target: AttentionItem["target"]) =>
    items.push({ severity, text, organisationId: null, organisationName: null, target });

  if (quality !== null && quality.platform != null) {
    const { runs, runs_failed_platform: failed, runs_degraded: degraded } = quality.platform;
    if (share(failed, runs) >= ATTENTION.failedPlatformShare) {
      platform("act", `${percentOf(failed, runs)} of reading runs failed for platform reasons (${formatNumber(failed)} of ${formatNumber(runs)}).`, "health");
    }
    if (share(degraded, runs) >= ATTENTION.degradedShare) {
      platform("check", `${percentOf(degraded, runs)} of reading runs finished without the AI card (${formatNumber(degraded)} of ${formatNumber(runs)}).`, "models");
    }
  }
  if (health) {
    const fresh = (health.reader ?? []).filter((report) => now.getTime() - Date.parse(report.seen_at) < NOT_REPORTING_MS);
    const down = fresh.filter((report) => report.state === "down").length;
    const key = fresh.filter((report) => report.state === "key_problem").length;
    if (down > 0) platform("act", `The scan reader is down on ${formatNumber(down)} ${down === 1 ? "worker" : "workers"}.`, "health");
    if (key > 0) platform("act", `The scan reader has a key problem on ${formatNumber(key)} ${key === 1 ? "worker" : "workers"}.`, "health");
    const waited = health.totals.oldest_in_progress_seconds;
    if (waited !== null && waited > ATTENTION.queueOldMinutes * 60) {
      platform("check", `The oldest document in progress has waited ${waitWords(waited)}.`, "health");
    }
  }
  if (quality === null || health === null) {
    platform("check", "Health or quality figures could not be loaded, so this list may be incomplete.", "health");
  }
  for (const org of reply.organisations) {
    const name = names.get(org.organisation_id) ?? null;
    for (const flag of orgFlags(org, reply, now)) {
      items.push({
        severity: flag.severity,
        text: `${name ?? "An organisation"}: ${flag.text}`,
        organisationId: org.organisation_id,
        organisationName: name,
        target: "organisation",
      });
    }
  }
  return riskOrder(items);
}

/** "Act now" before "Check"; a stable sort keeps each group's order. */
export function riskOrder<T extends { severity: Severity }>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => Number(a.severity === "check") - Number(b.severity === "check"));
}

const FORMULA_STARTS = ["=", "+", "-", "@", "\t", "\r"];

/** One cell as the backend's csv_cell and Python's csv writer: formula guard, then quotes when needed. */
function csvCell(value: string | number | null): string {
  if (value === null || (typeof value === "number" && !Number.isFinite(value))) return "";
  // A number we made is never a formula; a text cell starting like one gets a leading '.
  const text = typeof value === "number" ? String(value) : FORMULA_STARTS.some((start) => value.startsWith(start)) ? `'${value}` : value;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** The Overview's Download CSV (a client Blob): CRLF after every row, as the server's CSVs. */
export function csvOf(rows: readonly (readonly (string | number | null)[])[]): string {
  return rows.map((row) => `${row.map(csvCell).join(",")}\r\n`).join("");
}

export interface BoardLine { row: BoardRow; tooFew: boolean; pages: number; usdPer1000Pages: number | null; cachedShare: number | null }

/**
 * F4's board lines, most pages first, then by model. pages = AI + local + cached pages of
 * its runs. $ per 1,000 pages = scan_cost_usd per 1,000 pages_ai: the billed pages only
 * (cached and local pages cost nothing), so models compare on what a read page costs.
 * ponytail: cost outlives a deleted document and its pages do not, so this can read a little high.
 * cachedShare = cached_scan_calls / scan_calls (scan_calls counts every scan call, cached ones included); null without calls.
 */
export function boardRows(items: readonly BoardRow[]): BoardLine[] {
  return items
    .map((row) => ({
      row,
      tooFew: row.documents < BOARD_MIN_DOCUMENTS,
      pages: row.pages_ai + row.pages_local + row.pages_cached,
      usdPer1000Pages: usdPer1000Pages(row.scan_cost_usd, row.pages_ai),
      cachedShare: row.scan_calls > 0 ? row.cached_scan_calls / row.scan_calls : null,
    }))
    .sort((a, b) => b.pages - a.pages || (a.row.model < b.row.model ? -1 : a.row.model > b.row.model ? 1 : 0));
}

/** Which lines hold each column's best value; the table says it in a word ("lowest", "fastest", "fewest"). */
export interface BoardBest { per1000: boolean; seconds: boolean; failed: boolean; cost: boolean }

/** True at each smallest value, every tie included; all false unless 2 or more values are known. */
function lowest(values: readonly (number | null)[]): boolean[] {
  const known = values.filter((v): v is number => v !== null && Number.isFinite(v));
  const min = known.length >= 2 ? Math.min(...known) : null;
  return values.map((v) => v !== null && v === min);
}

/**
 * Board 12 P8: each Compare models column's best value. A line is ranked only on what its
 * row shows: no cost per page, speed or failures for a model that reads no pages or has
 * too few documents. Values are rounded as the table shows them, so two that read the same
 * tie. "Failed or without a card" ranks the two together, as a share of runs. An AI cost
 * only partly known is a floor, not a value, so it is not ranked.
 */
export function boardBest(lines: readonly BoardLine[]): BoardBest[] {
  const shown = (l: BoardLine) => (l.row.runs > 0 || l.pages > 0) && !l.tooFew; // BoardLineRow's `reads`, not too few
  const round = (value: number | null, by: number) => (value === null ? null : Math.round(value * by));
  const per1000 = lowest(lines.map((l) => (shown(l) ? round(l.usdPer1000Pages, 100) : null)));
  const seconds = lowest(lines.map((l) => (shown(l) ? round(l.row.seconds_per_page_p50, 10) : null)));
  const failed = lowest(
    lines.map((l) => (shown(l) && l.row.runs > 0 ? round((l.row.runs_failed + l.row.runs_degraded) / l.row.runs, 1000) : null)),
  );
  const cost = lowest(lines.map((l) => (l.row.cost_part_unknown ? null : round(l.row.cost_usd, 100))));
  return lines.map((_, i) => ({
    per1000: per1000[i] ?? false,
    seconds: seconds[i] ?? false,
    failed: failed[i] ?? false,
    cost: cost[i] ?? false,
  }));
}
