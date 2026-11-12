// Admin → Usage (model choice SP2, SP4): its states, the budget warnings and the
// tables in tokens, rendered in node from fixtures shaped like GET /admin/usage
// (lib/types UsageReport).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  budgetLine,
  indiaDay,
  usageKindText,
  usageRange,
  usageState,
} from "@/components/admin/logic";
import type { UsageState } from "@/components/admin/logic";
import {
  UsagePanel,
  UsageReportView,
  compactNumber,
  periodDays,
  periodText,
  shareText,
  usageTokens,
} from "@/components/admin/UsagePanel";
import type { UsageBudget, UsageKind, UsageReport, UsageTotals } from "@/lib/types";

const totals = (changes: Partial<UsageTotals> = {}): UsageTotals => ({
  calls: 40,
  cached_calls: 2,
  input_tokens: 120000,
  output_tokens: 30000,
  reasoning_tokens: 8000,
  cost_usd: 12.4,
  cost_estimated: false,
  cost_part_unknown: false,
  ...changes,
});

const budget = (spent: number, limit: number | null): UsageBudget => ({
  ai_budget_usd: limit,
  spent_this_month_usd: spent,
  spent_part_unknown: false,
  share: limit === null ? null : spent / limit,
});

const REPORT: UsageReport = {
  from: "2026-10-01",
  to: "2026-10-11",
  totals: totals(),
  by_user: [
    { ...totals({ cost_usd: 10, input_tokens: 1000, output_tokens: 500 }), user_id: "u1", user_name: "Ravi Sharma" },
    { ...totals({ cost_usd: 2.4, input_tokens: 90000, output_tokens: 10000 }), user_id: null, user_name: null },
  ],
  by_model: [{ ...totals(), model: "google/gemini-3.1-pro-preview", model_name: "Gemini 3.1 Pro" }],
  by_kind: [
    { ...totals({ cost_usd: 12 }), kind: "scan" },
    { ...totals({ cost_usd: 0.4, cost_estimated: true }), kind: "chat" },
  ],
  by_day: [
    { ...totals({ cost_usd: 8, input_tokens: 80000, output_tokens: 20000 }), day: "2026-10-10" },
    { ...totals({ cost_usd: 4, input_tokens: 40000, output_tokens: 10000 }), day: "2026-10-11" },
  ],
  top_documents: [
    { ...totals({ cost_usd: 3 }), document_id: "d1", name: "Invoice 42.pdf" },
    { ...totals({ cost_usd: 0.003 }), document_id: null, name: "Deleted document" },
  ],
  // Changed on purpose (features 25-27, 15 Oct 2026): GET /admin/usage sends no budget any more.
  ai_enabled: true,
};

const view = (state: UsageState, onRetry: () => void = () => undefined) =>
  renderToStaticMarkup(createElement(UsageReportView, { state, onRetry }));

/** The dates under the daily bars. */
const ticks = (html: string) =>
  Array.from(html.matchAll(/<span class="[^"]*tick_[^"]*"[^>]*>([^<]*)<\/span>/g), (match) => match[1]);

/** The first <button> in a React element tree. */
function findButton(node: ReactNode): ReactElement | null {
  if (Array.isArray(node)) {
    for (const child of node as ReactNode[]) {
      const found = findButton(child);
      if (found) return found;
    }
    return null;
  }
  if (!isValidElement(node)) return null;
  if (node.type === "button") return node;
  return findButton((node.props as { children?: ReactNode }).children);
}

describe("the Usage tab's states", () => {
  it("is loading until the report comes, with a skeleton and a spoken status", () => {
    expect(usageState(null, null)).toEqual({ kind: "loading" });
    const html = view({ kind: "loading" });
    expect(html).toContain('role="status"');
    expect(html).toContain("Loading the AI use");
    expect(html).toContain("skeleton");
  });

  it("says No AI use in this period when nothing was called", () => {
    const empty = { ...REPORT, totals: totals({ calls: 0, cost_usd: 0 }) };
    // Changed on purpose (QA P7, 4 Nov 2026): the empty state keeps its report, for "Pages read today".
    expect(usageState(empty, null)).toEqual({ kind: "empty", report: empty });
    expect(view({ kind: "empty", report: empty })).toContain("No AI use in this period.");
  });

  it("says AI is off on this server in keyless mode, not that nothing was used", () => {
    const keyless = { ...REPORT, totals: totals({ calls: 0, cost_usd: 0 }), ai_enabled: false };
    expect(usageState(keyless, null)).toEqual({ kind: "off", report: keyless });
    const html = view({ kind: "off", report: keyless });
    expect(html).toContain("AI is off on this server");
    expect(html).not.toContain("No AI use in this period.");
  });

  it("still shows what was spent before AI was turned off", () => {
    expect(usageState({ ...REPORT, ai_enabled: false }, null).kind).toBe("ready");
  });

  it("shows the error with a Retry button that asks again", () => {
    expect(usageState(REPORT, "The server is busy.")).toEqual({ kind: "error", message: "The server is busy." });
    let retried = 0;
    const onRetry = () => {
      retried += 1;
    };
    const state: UsageState = { kind: "error", message: "The server is busy." };
    const html = view(state, onRetry);
    expect(html).toContain('role="alert"');
    expect(html).toContain("The server is busy.");
    expect(html).toMatch(/<button[^>]*>.*Retry<\/button>/);
    const button = findButton(UsageReportView({ state, onRetry }));
    (button?.props as { onClick: () => void }).onClick();
    expect(retried).toBe(1);
  });

  // Changed on purpose (UI mockups board 7, 16 Oct 2026): the filter is "Person", not "User".
  it("starts loading, with labelled period and person selects, This month picked", () => {
    const html = renderToStaticMarkup(createElement(UsagePanel));
    expect(html).toMatch(/<label[^>]*for="([^"]+)-period"[^>]*>Period<\/label><select id="\1-period"/);
    expect(html).toMatch(/<label[^>]*for="([^"]+)-user"[^>]*>Person<\/label><select id="\1-user"/);
    expect(html).toContain('<option value="month" selected="">This month</option>');
    expect(html).toContain('<option value="custom">Custom</option>');
    expect(html).toContain("Everyone");
    expect(html).toContain("Loading the AI use");
    expect(html).not.toContain('type="date"'); // only for Custom
  });

  it("has two labelled native date boxes for Custom", () => {
    const source = readFileSync(join(__dirname, "..", "components/admin/UsagePanel.tsx"), "utf8");
    expect(source.match(/type="date"/g)).toHaveLength(2);
    expect(source).toMatch(/htmlFor=\{`\$\{id\}-from`\}>\s*From\s*<\/label>\s*<input\s+id=\{`\$\{id\}-from`\}/);
    expect(source).toMatch(/htmlFor=\{`\$\{id\}-to`\}>\s*To\s*<\/label>\s*<input\s+id=\{`\$\{id\}-to`\}/);
  });
});

describe("the report", () => {
  const html = view({ kind: "ready", report: REPORT });

  // Changed on purpose (UI quality council, 15 Oct 2026): Tushar's decisions (17:40, 18:38), org
  // admins see AI use in tokens only: no money, no spend, no budget.
  // Changed on purpose (UI mockups board 7, 16 Oct 2026): a short number with its unit, the
  // period and a day's average; the exact number stays on hover.
  it("has one tile for the tokens used, with the period and a day's average, and no money or budget", () => {
    expect(html).toContain("<span>Tokens in this period</span>");
    expect(html).toContain("1–11 Oct 2026</span></dt>");
    // input + output (output counts the thinking): 1,50,000 over the 11 days of the period.
    expect(html).toMatch(/title="1,50,000">150K <span[^>]*>tokens<\/span><\/dd>/);
    // Changed on purpose (UI audit, 16 Oct 2026): AD5, the average over the days with use, with how many they were.
    expect(html).toMatch(/>\s*Average per active day: [^<]+ \(\d+ of 11 days\)\s*<\/dd>/);
    expect(html).not.toMatch(/spen[dt]|cost|budget|[$₹]|\bRs\b|\bUSD\b/i);
    expect(html).not.toContain("Budget used");
  });

  it("adds input and output tokens, the thinking being part of the output", () => {
    expect(usageTokens(totals())).toBe(150000);
  });

  // Changed on purpose (UI mockups board 7, 16 Oct 2026): By kind of work has a Share column.
  it("has a captioned table each by day, user, kind and document, every header scope=col", () => {
    for (const caption of ["Tokens by day", "By user", "By kind of work", "Top documents"]) {
      expect(html).toMatch(new RegExp(`<caption[^>]*>${caption}</caption>`));
    }
    expect(html).not.toContain("By model");
    expect(html.match(/<th scope="col"/g)).toHaveLength(3 * 2 + 2 + 1);
    expect(html.match(/<th[ >]/g)).toHaveLength(3 * 2 + 2 + 1);
    expect(html).toMatch(/<th[^>]*>Person<\/th>/);
  });

  it("shows the tokens with tabular figures, the most first", () => {
    // Changed on purpose (UI quality council, 15 Oct 2026): tokens, not cost, in the "num" column.
    expect(html).toContain('class="num">1,500</td>');
    // The server sorts by cost; the screen by tokens, so system work (1,00,000) comes before Ravi (1,500).
    expect(html.indexOf("Removed user or system work")).toBeLessThan(html.indexOf("Ravi Sharma"));
  });

  it("names the user, the system, the kind and the documents in words, never a model", () => {
    expect(html).toContain("Ravi Sharma");
    expect(html).toContain("Removed user or system work");
    expect(html).not.toContain("Gemini");
    expect(html).toContain("Reading scanned pages");
    expect(html).toContain('<a href="/documents/d1">Invoice 42.pdf</a>');
    expect(html).toContain("Deleted document");
    expect(html).not.toContain('href="/documents/null"');
  });

  // Changed on purpose (UI mockups board 7, 16 Oct 2026): upright bars, one per day of the
  // period, with the busiest day and a few dates; the table is kept for screen readers only.
  it("draws an upright bar per day of the period with CSS, hidden from screen readers", () => {
    const chart = html.slice(html.search(/<div class="[^"]*chart_[^"]*" aria-hidden="true">/), html.indexOf('<div class="visually-hidden">'));
    expect(chart.length).toBeGreaterThan(0);
    const bars = chart.match(/<span class="[^"]*dayBar[^"]*" style="height:[^"]*"/g) ?? [];
    expect(bars).toHaveLength(11); // 1 to 11 Oct, days with no use included
    expect(bars.slice(0, 9).every((bar) => bar.includes("height:0"))).toBe(true);
    expect(bars[9]).toContain("height:max(2px, 100%)");
    expect(bars[10]).toContain("height:max(2px, 50%)");
    expect(chart).toMatch(/peak[^"]*" style="left:90%;transform:translateX\(-90%\)">100K · 10 Oct</);
    expect(ticks(chart)).toEqual(["1 Oct", "11 Oct"]); // 8 Oct would crowd 11 Oct
    expect(html).not.toMatch(/<canvas|role="img"/);
  });

  it("puts a date under the first and last day and each week between", () => {
    const month = { ...REPORT, from: "2026-10-01", to: "2026-10-31" };
    expect(ticks(view({ kind: "ready", report: month }))).toEqual(["1 Oct", "8 Oct", "15 Oct", "22 Oct", "31 Oct"]);
    const year = { ...REPORT, from: "2026-01-01", to: "2026-12-31" };
    expect(ticks(view({ kind: "ready", report: year })).length).toBeLessThanOrEqual(7);
  });

  it("keeps the days' numbers in a table that only screen readers get", () => {
    const hidden = html.slice(html.indexOf('<div class="visually-hidden">'));
    expect(hidden).toMatch(/^<div class="visually-hidden"><table[^>]*><caption>Tokens by day<\/caption>/);
    expect(hidden).toContain('class="num">1,00,000</td>');
    expect(hidden).toContain('class="num">50,000</td>');
  });

  it("gives each kind of work its share of the tokens, in words and a bar", () => {
    const kinds = html.slice(html.indexOf("By kind of work"), html.indexOf("Top documents"));
    expect(kinds).toMatch(
      /<td role="cell" data-label="Share"><span class="[^"]*barCell[^"]*"><span>50%<\/span><span class="[^"]*bar_[^"]*" aria-hidden="true" style="width:2.5rem"><\/span>/,
    );
    expect(html.match(/data-label="Share"/g)).toHaveLength(2); // the kinds only
  });

  // Changed on purpose (UI quality council, 15 Oct 2026): the CSV has cost and model columns, so it
  // is off the org admin's screen until the operator's money switch (follow-up PR).
  it("has no CSV download", () => {
    expect(html).not.toContain("usage.csv");
    expect(html).not.toContain("Download as CSV");
  });
});

// Changed on purpose (UI quality council, 15 Oct 2026): org admins see no budget (Tushar, 18:38), so
// the 80% / 100% warning tests went with the warning; the operator's money line stays.
describe("the operator's budget line, and rows with no user", () => {
  // The operator's console still shows this money line (components/operator/insights.ts).
  it("says when part of this month's spend is not known (R9)", () => {
    expect(budgetLine({ ...budget(5, 50), spent_part_unknown: true })).toBe(
      "$5.00 (part not known) of $50.00 this month",
    );
  });

  it("names a by-user row with no user as removed or system work (R13)", () => {
    const html = view({ kind: "ready", report: REPORT });
    expect(html).toContain(">Removed user or system work</td>");
  });

  it("says when the operator set no budget", () => {
    expect(budgetLine(budget(500, null))).toBe("No monthly AI budget is set.");
  });
});

describe("the period, in India time like the server", () => {
  // 11 Oct 2026, 01:00 in India is still 10 Oct in UTC.
  const NOW = Date.parse("2026-10-10T19:30:00Z");
  const none = { from: "", to: "" };

  it("takes today in India", () => {
    expect(indiaDay(NOW)).toBe("2026-10-11");
    expect(usageRange("today", none, NOW)).toEqual({ from: "2026-10-11", to: "2026-10-11" });
  });

  it("counts the last 7 days with today, and this month from the 1st", () => {
    expect(usageRange("week", none, NOW)).toEqual({ from: "2026-10-05", to: "2026-10-11" });
    expect(usageRange("week", none, Date.parse("2026-03-02T06:00:00Z"))).toEqual({
      from: "2026-02-24",
      to: "2026-03-02",
    });
    expect(usageRange("month", none, NOW)).toEqual({ from: "2026-10-01", to: "2026-10-11" });
  });

  it("takes the two dates for Custom, only once both are set with the start first", () => {
    expect(usageRange("custom", { from: "2026-09-01", to: "2026-09-30" }, NOW)).toEqual({
      from: "2026-09-01",
      to: "2026-09-30",
    });
    expect(usageRange("custom", { from: "2026-09-01", to: "" }, NOW)).toBeNull();
    expect(usageRange("custom", { from: "2026-09-30", to: "2026-09-01" }, NOW)).toBeNull();
    expect(usageRange("custom", { from: "2026-09-30", to: "2026-09-30" }, NOW)).not.toBeNull();
  });
});

describe("the Usage tab's numbers and dates", () => {
  it("shortens big numbers", () => {
    expect(compactNumber(4200000)).toBe("4.2M");
    expect(compactNumber(280000)).toBe("280K");
    expect(compactNumber(559123)).toBe("559K");
    expect(compactNumber(950)).toBe("950");
  });

  it("names the period short, across months and years too", () => {
    expect(periodText("2026-10-01", "2026-10-15")).toBe("1–15 Oct 2026");
    expect(periodText("2026-10-15", "2026-10-15")).toBe("15 Oct 2026");
    expect(periodText("2025-12-28", "2026-01-03")).toBe("28 Dec 2025\u2009–\u20093 Jan 2026");
  });

  it("counts every day of the period, both ends in, across a month's end", () => {
    expect(periodDays("2026-09-29", "2026-10-02")).toEqual(["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]);
    expect(periodDays("2026-10-11", "2026-10-11")).toEqual(["2026-10-11"]);
  });

  it("says a share in whole percent, a sliver as Under 1%", () => {
    expect(shareText(67, 100)).toBe("67%");
    expect(shareText(1, 1000)).toBe("Under 1%");
    expect(shareText(0, 100)).toBe("0%");
    expect(shareText(0, 0)).toBe("0%");
  });
});

describe("usageKindText", () => {
  it("names the kinds of work in words", () => {
    expect(usageKindText("scan")).toBe("Reading scanned pages");
    expect(usageKindText("embed")).toBe("Search indexing");
    expect(usageKindText("story")).toBe("Stories");
    expect(usageKindText("land")).toBe("Land records");
    // #488: an org admin never sees "ultra low cost", only that pages were read
    expect(usageKindText("ulc_scan")).toBe("Reading scanned pages");
  });

  it("has words for every kind the backend bills (kaagaz/ai/usage.py KINDS), never the raw kind", () => {
    const usage = readFileSync(join(__dirname, "..", "..", "backend", "kaagaz", "ai", "usage.py"), "utf8");
    const block = usage.slice(usage.indexOf("KINDS = ("), usage.indexOf(")", usage.indexOf("KINDS = (")));
    const kinds = [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1] as UsageKind);
    expect(kinds.length).toBeGreaterThan(10);
    for (const kind of kinds) expect(usageKindText(kind), kind).not.toBe(kind);
  });
});
