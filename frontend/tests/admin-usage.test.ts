// Admin → Usage (model choice SP2, SP4): its states, the budget warnings, the
// tables and the CSV link, rendered in node from fixtures shaped like GET
// /admin/usage (lib/types UsageReport).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement, isValidElement } from "react";
import type { ReactElement, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  budgetLine,
  budgetShareText,
  budgetWarning,
  indiaDay,
  usageKindText,
  usageRange,
  usageState,
} from "@/components/admin/logic";
import type { UsageState } from "@/components/admin/logic";
import { UsagePanel, UsageReportView } from "@/components/admin/UsagePanel";
import type { UsageParams } from "@/lib/api";
import type { UsageBudget, UsageReport, UsageTotals } from "@/lib/types";

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
    { ...totals({ cost_usd: 10 }), user_id: "u1", user_name: "Ravi Sharma" },
    { ...totals({ cost_usd: 2.4 }), user_id: null, user_name: null },
  ],
  by_model: [{ ...totals(), model: "google/gemini-3.1-pro-preview", model_name: "Gemini 3.1 Pro" }],
  by_kind: [
    { ...totals({ cost_usd: 12 }), kind: "scan" },
    { ...totals({ cost_usd: 0.4, cost_estimated: true }), kind: "chat" },
  ],
  by_day: [
    { ...totals({ cost_usd: 8 }), day: "2026-10-10" },
    { ...totals({ cost_usd: 4 }), day: "2026-10-11" },
  ],
  top_documents: [
    { ...totals({ cost_usd: 3 }), document_id: "d1", name: "Invoice 42.pdf" },
    { ...totals({ cost_usd: 0.003 }), document_id: null, name: "Deleted document" },
  ],
  budget: budget(12.4, 50),
  ai_enabled: true,
};

const PARAMS: UsageParams = { from: "2026-10-01", to: "2026-10-11", user_id: "u1" };

const view = (state: UsageState, onRetry: () => void = () => undefined, params: UsageParams = PARAMS) =>
  renderToStaticMarkup(createElement(UsageReportView, { state, params, onRetry }));

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
    expect(usageState(empty, null)).toEqual({ kind: "empty" });
    expect(view({ kind: "empty" })).toContain("No AI use in this period.");
  });

  it("says AI is off on this server in keyless mode, not that nothing was used", () => {
    const keyless = { ...REPORT, totals: totals({ calls: 0, cost_usd: 0 }), ai_enabled: false };
    expect(usageState(keyless, null)).toEqual({ kind: "off" });
    const html = view({ kind: "off" });
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
    const button = findButton(UsageReportView({ state, params: PARAMS, onRetry }));
    (button?.props as { onClick: () => void }).onClick();
    expect(retried).toBe(1);
  });

  it("starts loading, with labelled period and user selects, This month picked", () => {
    const html = renderToStaticMarkup(createElement(UsagePanel));
    expect(html).toMatch(/<label[^>]*for="([^"]+)-period"[^>]*>Period<\/label><select id="\1-period"/);
    expect(html).toMatch(/<label[^>]*for="([^"]+)-user"[^>]*>User<\/label><select id="\1-user"/);
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

  it("has tiles for the spend, the tokens and the budget used", () => {
    expect(html).toContain("AI spend</dt>");
    expect(html).toContain("$12.40");
    expect(html).toContain("1,50,000"); // 1,20,000 in + 30,000 out, Indian grouping
    expect(html).toContain("24%");
    expect(html).toContain("$12.40 of $50.00 this month");
  });

  it("has a captioned table each by user, model, kind, document and day, every header scope=col", () => {
    for (const caption of ["By user", "By model", "By kind of work", "Costliest documents", "Spend by day"]) {
      expect(html).toMatch(new RegExp(`<caption[^>]*>${caption}</caption>`));
    }
    expect(html.match(/<th scope="col"/g)).toHaveLength(4 * 4 + 2);
    expect(html.match(/<th[ >]/g)).toHaveLength(4 * 4 + 2);
  });

  it("shows the numbers with tabular figures", () => {
    expect(html).toContain('class="tabular">1,20,000</td>');
  });

  it("names the user, the system, the model, the kind and the documents in words", () => {
    expect(html).toContain("Ravi Sharma");
    expect(html).toContain("No user (work done by the system)");
    expect(html).toContain("Gemini 3.1 Pro");
    expect(html).toContain("Reading scanned pages");
    expect(html).toContain('<a href="/documents/d1">Invoice 42.pdf</a>');
    expect(html).toContain("Deleted document");
    expect(html).not.toContain('href="/documents/null"');
  });

  it("says estimated in words, and under a cent as < $0.01", () => {
    expect(html).toContain("≈ $0.40 (estimated)");
    expect(html).toContain("&lt; $0.01");
  });

  it("draws each day's bar with CSS, hidden from screen readers, the number in the cell", () => {
    const bars = html.match(/<span[^>]*aria-hidden="true"[^>]*style="width:[^"]*"/g) ?? [];
    expect(bars).toHaveLength(2);
    expect(bars[0]).toContain("width:100%");
    expect(bars[1]).toContain("width:50%");
    expect(html).toContain("<span>$8.00</span>");
    expect(html).not.toMatch(/<canvas|role="img"/);
  });

  it("links the CSV with the same period and user", () => {
    expect(html).toMatch(
      /<a [^>]*href="\/api\/admin\/usage\.csv\?from=2026-10-01&amp;to=2026-10-11&amp;user_id=u1"[^>]*download/,
    );
  });

  it("leaves the user out of the CSV link for everyone", () => {
    const everyone = view({ kind: "ready", report: REPORT }, undefined, { from: "2026-10-11", to: "2026-10-11" });
    expect(everyone).toContain('href="/api/admin/usage.csv?from=2026-10-11&amp;to=2026-10-11"');
  });
});

describe("the budget (SP4, warn only)", () => {
  it("says nothing under 80%", () => {
    expect(budgetWarning(budget(39.99, 50))).toBeNull();
    expect(budgetShareText(budget(39.99, 50))).toBe("79%");
  });

  it("warns at 80%, as a status line", () => {
    expect(budgetWarning(budget(40, 50))).toBe(
      "This month's AI spend is at 80% of the budget ($40.00 of $50.00 this month).",
    );
    const html = view({ kind: "ready", report: { ...REPORT, budget: budget(42, 50) } });
    expect(html).toMatch(/<p[^>]*role="status"[^>]*>.*at 84% of the budget/);
  });

  it("warns at 100% and over, saying work goes on", () => {
    const full = budgetWarning(budget(50, 50));
    expect(full).toContain("has reached the budget ($50.00 of $50.00 this month)");
    expect(full).toContain("Work goes on");
    expect(budgetWarning(budget(61, 50))).toContain("has reached the budget ($61.00 of $50.00 this month)");
    const html = view({ kind: "ready", report: { ...REPORT, budget: budget(61, 50) } });
    expect(html).toMatch(/<p[^>]*role="status"[^>]*>.*has reached the budget/);
    expect(html).toContain("122%");
  });

  it("says when part of this month's spend is not known (R9)", () => {
    expect(budgetLine({ ...budget(5, 50), spent_part_unknown: true })).toBe(
      "$5.00 (part not known) of $50.00 this month",
    );
    const html = view({ kind: "ready", report: { ...REPORT, totals: totals({ cost_part_unknown: true }) } });
    expect(html).toContain("$12.40 (part not known)");
  });

  it("has no warning, and says so, when there is no budget", () => {
    expect(budgetWarning(budget(500, null))).toBeNull();
    expect(budgetLine(budget(500, null))).toBe("No monthly AI budget is set.");
    const html = view({ kind: "ready", report: { ...REPORT, budget: budget(500, null) } });
    expect(html).toContain("No budget");
    expect(html).not.toContain('role="status"');
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

describe("usageKindText", () => {
  it("names the kinds of work in words", () => {
    expect(usageKindText("scan")).toBe("Reading scanned pages");
    expect(usageKindText("embed")).toBe("Search indexing");
    expect(usageKindText("story")).toBe("Stories");
  });
});
