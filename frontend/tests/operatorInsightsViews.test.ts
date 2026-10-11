// Operator insights screens (council 11 Oct 2026): Overview, Quality and Compare
// models rendered in node from fixtures shaped like GET /operator/insights, /quality
// and /model-board (lib/types). Each state: loading, error, empty, keyless and full;
// captions, decoration hidden from screen readers, no canvas, words beside colour.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ModelBoardScreen } from "@/components/operator/ModelBoard";
import type { BoardData } from "@/components/operator/ModelBoard";
import { OperatorConsole } from "@/components/operator/OperatorConsole";
import { OverviewScreen, overviewCsv } from "@/components/operator/OverviewView";
import type { LoadState, OverviewData } from "@/components/operator/OverviewView";
import { QualityScreen } from "@/components/operator/QualityView";
import type { QualityData } from "@/components/operator/QualityView";
import type { BoardRow, InsightDay, InsightsReply, OrgInsight, Organisation, QualityRow } from "@/lib/types";

const LOADED_AT = Date.parse("2026-10-11T10:00:00Z");
const MINUTE = 60_000;
const noop = () => undefined;

const org = (id: string, name: string): Organisation => ({
  id,
  name,
  status: "active",
  disabled_at: null,
  delete_after: null,
  created_at: "2026-09-01T00:00:00Z",
  users: { admins: 1, reviewers: 1, members: 3 },
  documents: 40,
  pages: 400,
  storage_bytes: 1000,
  last_activity_at: "2026-10-11T09:00:00Z",
});

const health = (id: string, changes: Partial<OrgInsight> = {}): OrgInsight => ({
  organisation_id: id,
  active_users_7d: 4,
  active_users_30d: 6,
  active_users_prev_30d: 6,
  answers_30d: 50,
  not_found_30d: 5,
  open_review: 3,
  oldest_open_review_at: "2026-10-10T10:00:00Z",
  ai_budget_usd: 50,
  month_cost_usd: 31.2,
  month_cost_estimated: false,
  month_cost_part_unknown: false,
  ...changes,
});

const day = (id: string, date: string, changes: Partial<InsightDay> = {}): InsightDay => ({
  organisation_id: id,
  day: date,
  uploads: 5,
  sign_ins: 3,
  questions: 20,
  corrections: 1,
  runs: 6,
  runs_failed: 1,
  pages: 60,
  scan_pages: 30,
  cached_pages: 2,
  cost_usd: 3,
  cost_scan_usd: 2,
  cost_documents_usd: 2.5,
  cost_questions_usd: 0.5,
  cost_estimated: false,
  cost_part_unknown: false,
  ...changes,
});

const reply = (changes: Partial<InsightsReply> = {}): InsightsReply => ({
  from: "2026-09-12",
  to: "2026-10-11",
  previous_from: "2026-08-13",
  ai_enabled: true,
  month: { from: "2026-10-01", today: "2026-10-11", days_in_month: 31 },
  // Bharat Steel has spent past its budget: "Act now" in Needs attention.
  organisations: [health("o1"), health("o2", { ai_budget_usd: 10, month_cost_usd: 12 })],
  daily: [day("o1", "2026-08-20"), day("o1", "2026-10-10"), day("o2", "2026-10-11", { uploads: 9 })],
  ...changes,
});

const ORGS = [org("o1", "Acme Traders"), org("o2", "Bharat Steel")];
const data = (changes: Partial<OverviewData> = {}): OverviewData => ({
  reply: reply(),
  orgs: ORGS,
  quality: null,
  health: null,
  loadedAt: LOADED_AT,
  ...changes,
});

function overview(state: LoadState<OverviewData>, now = LOADED_AT) {
  return renderToStaticMarkup(
    createElement(OverviewScreen, { days: 30, onDays: noop, state, onRefresh: noop, now, openOrg: noop, openTab: noop }),
  );
}

const ready = <T>(value: T): LoadState<T> => ({ kind: "ready", data: value });
const LOADING = { kind: "loading" } as const;
const FAILED = { kind: "error", message: "Could not reach Kaagaz." } as const;

/** Decoration only: every svg hidden from screen readers, no canvas, no role="img". */
function expectDecorationHidden(html: string) {
  const svgs = html.match(/<svg[^>]*>/g) ?? [];
  for (const svg of svgs) expect(svg).toContain('aria-hidden="true"');
  expect(html).not.toMatch(/<canvas|role="img"/);
}

describe("Overview", () => {
  it("is the console's first, default tab", () => {
    const html = renderToStaticMarkup(createElement(OperatorConsole));
    expect(html).toMatch(/<button[^>]*id="operator-tab-overview"[^>]*aria-selected="true"/);
    expect(html).toContain("Loading the overview…");
  });

  it("says it is loading in the one status line, with a delayed skeleton", () => {
    const html = overview(LOADING);
    expect(html.match(/role="status"/g)).toHaveLength(1);
    expect(html).toContain("Loading the overview…");
    expect(html).toMatch(/class="delayed[^"]*" aria-hidden="true"/);
    expect(html).not.toContain("Figures as of");
  });

  it("says what failed, why, and Try again", () => {
    const html = overview(FAILED);
    expect(html).toContain('role="alert"');
    expect(html).toContain("Could not load the overview.");
    expect(html).toContain("Could not reach Kaagaz.");
    expect(html).toContain("Try again");
  });

  it("says no organisations yet on an empty platform, with no CSV", () => {
    const html = overview(ready(data({ orgs: [], reply: reply({ organisations: [], daily: [] }) })));
    expect(html).toContain("No organisations yet");
    expect(html).not.toContain("Download CSV");
    expect(html).not.toContain("<table");
  });

  it("says AI is off on a keyless server, and shows no budget meter or cost", () => {
    const keyless = reply({
      ai_enabled: false,
      organisations: [health("o1", { ai_budget_usd: null, month_cost_usd: null })],
      daily: [day("o1", "2026-10-10", { cost_usd: null, cost_documents_usd: null, cost_questions_usd: null })],
    });
    const html = overview(ready(data({ reply: keyless })));
    expect(html).toContain("AI is off on this server");
    expect(html).not.toContain("<meter");
    expect(html).not.toContain("Month-end forecast");
  });

  describe("with figures", () => {
    const html = overview(ready(data()));

    it("shows when the figures are from, Refresh and a plain Download CSV button", () => {
      expect(html).toContain("Figures as of");
      expect(html).toMatch(/<button type="button" class="btn">.*Refresh<\/button>/);
      expect(html).toMatch(/<button type="button" class="btn">Download CSV<\/button>/);
      expect(html).not.toContain("over 15 minutes old");
    });

    it("says the figures are old after 15 minutes, never refreshing on its own", () => {
      expect(overview(ready(data()), LOADED_AT + 14 * MINUTE)).not.toContain("over 15 minutes old");
      expect(overview(ready(data()), LOADED_AT + 15 * MINUTE)).toContain("over 15 minutes old");
    });

    it("lists the 8 tiles as a description list", () => {
      const labels = [...html.matchAll(/<dt[^>]*>([^<]*)<\/dt>/g)].map((match) => match[1]);
      expect(labels).toEqual([
        "Active organisations",
        "Files uploaded",
        "Pages read",
        "Questions asked",
        "AI spend",
        "$ per 1,000 pages",
        "$ per 100 questions",
        "Failed runs",
      ]);
      expect(html).toContain("Month-end forecast");
    });

    it("draws a line per tile as hidden decoration, never a canvas or role=img", () => {
      expect(html.match(/<polyline/g)?.length).toBe(8);
      expectDecorationHidden(html);
    });

    it("says Act now with an icon and the words, and opens the organisation by its name", () => {
      expect(html).toMatch(/data-severity="act"><svg[^>]*aria-hidden="true"[^>]*>.*?<\/svg>Act now/);
      expect(html).toContain('aria-label="Open Bharat Steel in Organisations"');
    });

    it("names the organisation once in its line", () => {
      expect(html).toContain("Bharat Steel: AI spend has reached its budget");
      expect(html).not.toContain("Bharat Steel: Bharat Steel:");
      expect(html).not.toMatch(/<strong>Bharat Steel: <\/strong>Bharat Steel/);
    });

    it("captions the organisations table and marks its sorted column", () => {
      expect(html).toMatch(/<caption[^>]*>Organisations at a glance/);
      expect(html.match(/aria-sort=/g)).toHaveLength(1);
      expect(html).toMatch(/<th[^>]*aria-sort="descending"[^>]*>Flags<\/th>/);
      expect(html).toContain('<label for="overview-sort">Sort by</label>');
    });

    it("puts a budget meter beside its words", () => {
      expect(html).toMatch(/<meter[^>]*min="0"[^>]*><\/meter><span>\$31\.20 of \$50\.00<\/span>/);
      const meter = html.match(/<meter[^>]*aria-label="Budget used by Acme Traders"[^>]*>/)?.[0] ?? "";
      for (const part of ['max="50"', 'low="40"', 'high="50"', 'value="31.2"']) expect(meter, part).toContain(part);
      // Past the budget: the meter reaches the spend, the high mark stays at the budget.
      const over = html.match(/<meter[^>]*aria-label="Budget used by Bharat Steel"[^>]*>/)?.[0] ?? "";
      for (const part of ['max="12"', 'high="10"', 'value="12"']) expect(over, part).toContain(part);
    });
  });

  it("shows 5 items that need attention, then Show all", () => {
    const many = Array.from({ length: 7 }, (_, index) => org(`o${index}`, `Org ${index}`));
    const over = reply({
      organisations: many.map((item) => health(item.id, { ai_budget_usd: 10, month_cost_usd: 20 })),
    });
    const html = overview(ready(data({ orgs: many, reply: over })));
    expect(html.match(/aria-label="Open Org \d in Organisations"/g)).toHaveLength(5);
    expect(html).toMatch(/<button[^>]*aria-expanded="false"[^>]*>Show all \d+<\/button>/);
  });

  it("downloads the organisations as CSV, a leading = never starting a cell", () => {
    const csv = overviewCsv(data({ orgs: [org("o1", "=HYPERLINK(1)"), ORGS[1] as Organisation] }));
    expect(csv.split(/\r?\n/)[0]).toContain("Organisation");
    expect(csv).toContain("Bharat Steel");
    expect(csv).toContain("HYPERLINK(1)");
    expect(csv).not.toMatch(/(^|,)"?=/m);
  });
});

// --- Quality --------------------------------------------------------------------------

const quality = (changes: Partial<QualityRow> = {}): QualityRow => ({
  organisation_id: null,
  runs: 120,
  runs_failed: 6,
  runs_failed_platform: 2,
  runs_degraded: 3,
  documents_run: 100,
  pages_cells: 40,
  pages_text_layer: 300,
  pages_ocr: 500,
  pages_blank: 10,
  pages_ai: 420,
  pages_local: 50,
  pages_cached: 30,
  pages_unreadable: 12,
  ready_documents: 100,
  ready_p50_seconds: 90,
  ready_p95_seconds: 600,
  answers: 200,
  answered: 150,
  not_found: 40,
  unchecked: 10,
  answers_verified: 120,
  answers_fixed_query: 30,
  citations: 300,
  feedback: 25,
  feedback_up: 20,
  values_total: 1000,
  values_needs_review: 80,
  corrections: 40,
  corrections_second: 10,
  corrections_typed: 5,
  ...changes,
});

const qualityData = (changes: Partial<QualityData["reply"]> = {}): QualityData => ({
  reply: {
    from: "2026-09-12",
    to: "2026-10-11",
    ai_enabled: true,
    platform: quality(),
    organisations: [quality({ organisation_id: "o1" }), quality({ organisation_id: "o2", answers: 8, answered: null, not_found: null })],
    ...changes,
  },
  names: new Map([
    ["o1", "Acme Traders"],
    ["o2", "Bharat Steel"],
  ]),
});

const qualityHtml = (state: LoadState<QualityData>) =>
  renderToStaticMarkup(createElement(QualityScreen, { days: 30, onDays: noop, state, onRetry: noop }));

describe("Quality", () => {
  it("loads in one status line and fails with Try again", () => {
    expect(qualityHtml(LOADING)).toContain("Loading the quality figures…");
    const failed = qualityHtml(FAILED);
    expect(failed).toContain("Could not load the quality figures.");
    expect(failed).toContain("Try again");
  });

  it("says nothing was read on an empty platform", () => {
    const empty = quality({ runs: 0, answers: 0, corrections: 0 });
    const html = qualityHtml(ready(qualityData({ platform: empty, organisations: [] })));
    expect(html).toContain("Nothing was read, asked or reviewed in this period.");
    expect(html).not.toContain("<table");
  });

  it("says AI is off on a keyless server", () => {
    expect(qualityHtml(ready(qualityData({ ai_enabled: false })))).toContain("AI is off on this server");
  });

  describe("with figures", () => {
    const html = qualityHtml(ready(qualityData()));

    it("shows reading, answers and review, the times in words", () => {
      for (const heading of ["Reading", "Answers", "Review"]) expect(html).toContain(`>${heading}</h3>`);
      for (const label of ["Unreadable pages", "Read on the backup", "Failed, caused by the platform", "Ready without a document card", "Read more than once"]) {
        expect(html, label).toContain(label);
      }
      expect(html).toContain("Upload to ready, typical</dt>");
      expect(html).toContain("1 minute");
      expect(html).toContain("Upload to ready, slowest 5%</dt>");
      expect(html).toContain("10 minutes");
      expect(html).toContain("not always better");
    });

    it("captions the per-organisation table and rates too few as too few", () => {
      expect(html).toMatch(/<caption[^>]*>Per organisation<\/caption>/);
      expect(html).toContain("Too few to rate (8)");
    });

    it("keeps the bars as hidden decoration and footnotes deleted documents", () => {
      expectDecorationHidden(html);
      expect(html).toContain("Documents deleted for good are not counted");
    });
  });

  it("says too few to rate for a suppressed outcome, never 0%", () => {
    const suppressed = quality({ answers: 8, answered: null, not_found: null, unchecked: null, answers_verified: null, citations: null });
    const html = qualityHtml(ready(qualityData({ platform: suppressed, organisations: [] })));
    expect(html).toContain("Answer mix: Too few to rate (8)");
    expect(html).toContain("Too few to rate (8)");
  });
});

// --- Compare models ----------------------------------------------------------------------

const board = (model: string, changes: Partial<BoardRow> = {}): BoardRow => ({
  model,
  documents: 40,
  runs: 45,
  runs_failed: 1,
  runs_degraded: 1,
  pages_ai: 400,
  pages_local: 20,
  pages_cached: 30,
  seconds_per_page_p50: 4.2,
  seconds_per_page_p95: 11,
  calls: 470,
  scan_calls: 420,
  cached_scan_calls: 30,
  input_tokens: 900000,
  output_tokens: 200000,
  cost_usd: 6.5,
  scan_cost_usd: 6,
  cost_estimated: false,
  cost_part_unknown: false,
  ocr_pages: 450,
  pages_disagree: 18,
  pages_refused: 2,
  pages_fell_back: 20,
  pages_unpinned: 5,
  ...changes,
});

const boardData = (changes: Partial<BoardData["reply"]> = {}): BoardData => ({
  reply: {
    from: "2026-09-12",
    to: "2026-10-11",
    ai_enabled: true,
    items: [board("google/gemini-3.1-pro-preview"), board("google/gemini-3-flash", { documents: 12, runs: 12 })],
    ...changes,
  },
  names: new Map([["google/gemini-3.1-pro-preview", "Gemini 3.1 Pro"]]),
});

const boardHtml = (state: LoadState<BoardData>) =>
  renderToStaticMarkup(createElement(ModelBoardScreen, { days: 30, onDays: noop, state, onRetry: noop }));

describe("Compare models", () => {
  it("says it is observational, not an A/B test, in every state", () => {
    for (const state of [LOADING, FAILED, ready(boardData())]) {
      const html = boardHtml(state);
      expect(html).toContain("Compare models");
      expect(html).toMatch(/observational, not an A\/B test/i);
    }
    expect(boardHtml(LOADING)).toContain("Loading the model comparison…");
    expect(boardHtml(FAILED)).toContain("Could not load the model comparison.");
  });

  it("says when no model read anything", () => {
    expect(boardHtml(ready(boardData({ items: [] })))).toContain("No model read or answered anything in this period.");
  });

  it("shows only this server's reader when AI is off", () => {
    const html = boardHtml(ready(boardData({ ai_enabled: false, items: [board("rapidocr", { cost_usd: null, scan_cost_usd: null })] })));
    expect(html).toContain("AI is off on this server");
    expect(html).toContain("RapidOCR (this server)");
    expect(html).toContain("Not known");
  });

  describe("with models", () => {
    const html = boardHtml(ready(boardData()));
    const rows = html.split("<tr").slice(2).map((row) => row.split("</tr>")[0] ?? ""); // after the header row
    // Rows by their model, not their place: boardRows orders a tie on pages by model id.
    const pro = rows.find((row) => row.includes("google/gemini-3.1-pro-preview")) ?? "";
    const flash = rows.find((row) => row.includes("google/gemini-3-flash")) ?? "";

    it("captions the table and names a model from the catalogue", () => {
      expect(html).toMatch(/<caption[^>]*>Compare models, observational<\/caption>/);
      expect(pro).toContain("Gemini 3.1 Pro");
      for (const label of ["Pages", "$ per 1,000 pages (est.)", "Cache saved", "Seconds per page", "Page signals", "AI cost"]) {
        expect(html, label).toContain(`>${label}</th>`);
      }
    });

    it("compares a model with 30 documents or more, the speed typical and slowest 5%", () => {
      expect(pro).not.toMatch(/too few/i);
      expect(pro).toContain("typical 4.2 s");
      expect(pro).toContain("slowest 5% 11 s");
      expect(pro).toContain("Checker disagreement");
      expect(pro).toContain("$6.50");
    });

    it("says too few for a model under 30 documents, keeping its pages and cost", () => {
      expect(flash).toMatch(/too few/i);
      expect(flash).toContain("google/gemini-3-flash");
      expect(flash).not.toContain("Checker disagreement");
      expect(flash).toContain("$6.50");
    });

    it("keeps decoration hidden and never a canvas", () => {
      expectDecorationHidden(html);
    });
  });
});
