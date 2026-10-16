// Operator insights screens (council 11 Oct 2026): Overview, Quality and Compare
// models rendered in node from fixtures shaped like GET /operator/insights, /quality
// and /model-board (lib/types). Each state: loading, error, empty, keyless and full;
// captions, decoration hidden from screen readers, no canvas, words beside colour.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelBoardScreen } from "@/components/operator/ModelBoard";
import type { BoardData } from "@/components/operator/ModelBoard";
import { OperatorConsole } from "@/components/operator/OperatorConsole";
import { OverviewScreen, downloadCsv, overviewCsv, shownState } from "@/components/operator/OverviewView";
import type { LoadState, OverviewData } from "@/components/operator/OverviewView";
import { QualityScreen } from "@/components/operator/QualityView";
import type { QualityData } from "@/components/operator/QualityView";
import type { BoardRow, InsightDay, InsightsReply, OrgInsight, Organisation, PlatformHealth, QualityRow } from "@/lib/types";

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
  autofile_granted: false,
  autofile_on: false,
  two_step_granted: false,
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
  last_active_day: "2026-10-10",
  ...changes,
});

const day =(id: string, date: string, changes: Partial<InsightDay> = {}): InsightDay => ({
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

const HEALTH: PlatformHealth = {
  organisations: [],
  totals: { documents_by_status: {}, failed_by_code: {}, in_progress: 0, oldest_in_progress_seconds: null },
  purges: { purged: 0, last_purged_at: null, swept: 0, last_swept_at: null },
};

/** One tile's value, notes and change: from its label to the end of its <dd>. */
const tile = (html: string, label: string) => html.split(`>${label}</dt>`)[1]?.split("</dd>")[0] ?? "";

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
    expect(html).toContain("Loading the overview\u2026");
  });

  it("says it is loading in the one status line, with a delayed skeleton", () => {
    const html = overview(LOADING);
    expect(html.match(/role="status"/g)).toHaveLength(1);
    expect(html).toContain("Loading the overview\u2026");
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
        "Pages processed",
        "Questions asked",
        "AI spend",
        "$ per 1,000 pages",
        "$ per 100 questions",
        "Failed runs",
      ]);
      expect(html).toContain("Month-end forecast");
      expect(tile(html, "Pages processed")).toContain("(re-reads included)");
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

  it("shows no budget meter when the month's spend is not known, only the words", () => {
    const unknown = reply({ organisations: [health("o1", { month_cost_usd: null })] });
    const html = overview(ready(data({ reply: unknown })));
    expect(html).not.toContain("<meter");
    expect(html).toContain("Not known of $50.00");
  });

  it("says money changes on any dollar base, and a unit cost's on its pages or questions before", () => {
    // Before (1 Sep): 4 pages, 40 questions, $3 spent. Now (10 Oct): 60 pages, 40 questions, $2.10.
    const money = reply({
      organisations: [health("o1")],
      daily: [
        day("o1", "2026-09-01", { pages: 4, questions: 40, cost_usd: 3, cost_documents_usd: 1, cost_questions_usd: 1.2 }),
        day("o1", "2026-10-10", { pages: 60, questions: 40, cost_usd: 2.1, cost_documents_usd: 3, cost_questions_usd: 0.84 }),
      ],
    });
    const html = overview(ready(data({ reply: money })));
    expect(tile(html, "AI spend")).toContain("down 30%");
    expect(tile(html, "$ per 100 questions")).toContain("down 30%"); // $3.00 to $2.10 per 100 questions
    expect(tile(html, "$ per 1,000 pages")).toContain("too few to compare"); // only 4 pages before
  });

  it("keeps the figures, Refresh and Download CSV while refreshing, and says so", () => {
    const html = overview({ kind: "ready", data: data(), refreshing: true });
    expect(html).toMatch(/role="status">Refreshing\u2026<\/p>/);
    expect(html).toContain("Figures as of");
    expect(html).toContain(", refreshing\u2026");
    expect(html).toContain("Download CSV");
    expect(html).toContain("<table");
    expect(html).not.toContain('class="delayed');
    expect(overview(ready(data()))).not.toContain("refreshing\u2026");
  });

  it("can take focus on its heading, so Try again does not drop focus to the page", () => {
    expect(overview(FAILED)).toMatch(/<h2 id="overview-heading" tabindex="-1"/);
  });

  it("never says nothing needs attention when quality or health could not be loaded", () => {
    const calm = reply({ organisations: [health("o1", { ai_budget_usd: null })], daily: [] });
    const missing = overview(ready(data({ orgs: [ORGS[0] as Organisation], reply: calm })));
    expect(missing).toContain("Health or quality figures could not be loaded, so this list may be incomplete.");
    expect(missing).not.toContain("Nothing needs attention now.");
    const loaded = overview(ready(data({ orgs: [ORGS[0] as Organisation], reply: calm, quality: qualityData().reply, health: HEALTH })));
    expect(loaded).toContain("Nothing needs attention now.");
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

  describe("Download CSV", () => {
    afterEach(() => {
      vi.useRealTimers();
      vi.unstubAllGlobals();
      vi.restoreAllMocks();
    });

    it("revokes the file's address only after the click has started the download", () => {
      vi.useFakeTimers();
      const link = { href: "", download: "", click: vi.fn() };
      vi.stubGlobal("document", { createElement: () => link });
      vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:overview");
      const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
      downloadCsv(data());
      expect(link.click).toHaveBeenCalledTimes(1);
      expect(link.download).toBe("kaagaz-overview-2026-09-12-to-2026-10-11.csv");
      expect(revoke).not.toHaveBeenCalled();
      vi.runAllTimers();
      expect(revoke).toHaveBeenCalledWith("blob:overview");
    });
  });

  it("downloads the organisations as CSV, a leading = never starting a cell", () => {
    const csv = overviewCsv(data({ orgs: [org("o1", "=HYPERLINK(1)"), ORGS[1] as Organisation] }));
    expect(csv.split(/\r?\n/)[0]).toContain("Organisation");
    expect(csv).toContain("Bharat Steel");
    expect(csv).toContain("HYPERLINK(1)");
    expect(csv).not.toMatch(/(^|,)"?=/m);
  });
});

describe("shownState (useLoad)", () => {
  const figures = ready("figures");

  it("is loading on the first load and on a new period", () => {
    expect(shownState(null, 30, 0)).toEqual(LOADING);
    expect(shownState({ days: 30, attempt: 0, state: figures }, 7, 0)).toEqual(LOADING);
  });

  it("shows its own result once it came", () => {
    expect(shownState({ days: 30, attempt: 2, state: figures }, 30, 2)).toEqual(figures);
    expect(shownState({ days: 30, attempt: 2, state: FAILED }, 30, 2)).toEqual(FAILED);
  });

  it("keeps the last figures, marked refreshing, while Refresh reloads the same period", () => {
    expect(shownState({ days: 30, attempt: 0, state: figures }, 30, 1)).toEqual({ kind: "ready", data: "figures", refreshing: true });
  });

  it("is loading again on Try again after an error", () => {
    expect(shownState({ days: 30, attempt: 0, state: FAILED }, 30, 1)).toEqual(LOADING);
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
    expect(qualityHtml(LOADING)).toContain("Loading the quality figures\u2026");
    const failed = qualityHtml(FAILED);
    expect(failed).toContain("Could not load the quality figures.");
    expect(failed).toContain("Try again");
  });

  it("can take focus on its heading for Try again, and says when it is refreshing", () => {
    expect(qualityHtml(FAILED)).toMatch(/<h2 id="quality-heading" tabindex="-1"/);
    const refreshing = qualityHtml({ kind: "ready", data: qualityData(), refreshing: true });
    expect(refreshing).toMatch(/role="status">Refreshing\u2026<\/p>/);
    expect(refreshing).toContain("<table");
  });

  it("says nothing was read when there is no platform row (null)", () => {
    const html = qualityHtml(ready(qualityData({ platform: null, organisations: [] })));
    expect(html).toContain("Nothing was read, asked or reviewed in this period.");
    expect(html).not.toContain("<table");
  });

  it("says nothing was read on an empty platform", () => {
    const empty = quality({ runs: 0, answers: 0, corrections: 0 });
    const html = qualityHtml(ready(qualityData({ platform: empty, organisations: [] })));
    expect(html).toContain("Nothing was read, asked or reviewed in this period.");
    expect(html).not.toContain("<table");
  });

  it("is not empty when only a small organisation asked (its answers are left out of the platform row)", () => {
    const empty = quality({ runs: 0, answers: 0, corrections: 0 });
    const small = { ...quality({ runs: 0, answers: 3, corrections: 0 }), organisation_id: "org-small" };
    const html = qualityHtml(ready(qualityData({ platform: empty, organisations: [small] })));
    expect(html).not.toContain("Nothing was read, asked or reviewed in this period.");
    expect(html).toContain("only organisations with 10 or more of each");
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
    expect(boardHtml(FAILED)).toContain("Could not load the model comparison.");
  });

  it("has no status line of its own (the Models tab keeps one), only the loading skeleton", () => {
    const html = boardHtml(LOADING);
    expect(html).not.toContain('role="status"');
    expect(html).toMatch(/class="delayed[^"]*" aria-hidden="true"/);
  });

  it("can take focus on its heading for Try again", () => {
    expect(boardHtml(FAILED)).toMatch(/<h2 id="board-heading" tabindex="-1"/);
  });

  it("shows no misleading n for a suppressed page signal or speed, and a dash for no cache share", () => {
    const quiet = board("google/gemini-3.1-pro-preview", {
      ocr_pages: 450,
      pages_disagree: null,
      seconds_per_page_p95: null,
      scan_calls: 0,
      cached_scan_calls: 0,
    });
    const html = boardHtml(ready(boardData({ items: [quiet] })));
    expect(html).toContain("Checker disagreement: Too few scanned documents to rate");
    expect(html).not.toContain("Too few to rate (450)");
    expect(html).toContain("slowest 5%: too few timed runs");
    expect(html).not.toContain("Too few to rate (45)");
    // Changed on purpose (board 8 P2, 16 Oct 2026): figure columns are .num, lined up at the end.
    expect(html).toContain('data-label="Cache saved" class="num">\u2014</td>');
    expect(html).not.toContain("None of scan calls");
  });

  it("lines figures and their headers up at the end, the words columns at the start (board 8 P2)", () => {
    const html = boardHtml(ready(boardData()));
    for (const label of ["Pages", "$ per 1,000 pages (est.)", "Cache saved", "Seconds per page", "Failed or without a card", "AI cost"]) {
      expect(html, label).toContain(`class="num">${label}</th>`);
      expect(html, label).toContain(`data-label="${label}" class="num">`);
    }
    expect(html).toContain('role="columnheader">Model</th>');
    expect(html).toContain('role="columnheader">Page signals</th>');
  });

  it("shows a model id in mono, and its job under it when the catalogue gives one (board 8 P3)", () => {
    const jobs = new Map([["google/gemini-3.1-pro-preview", "Scan default (High) · chat default (Medium)"]]);
    const html = boardHtml(ready({ ...boardData(), jobs }));
    expect(html).toMatch(/<span class="[^"]*statusNote[^"]* [^"]*modelId[^"]*">google\/gemini-3.1-pro-preview<\/span>/);
    expect(html).toContain("Scan default (High) · chat default (Medium)</span>");
    expect(boardHtml(ready(boardData()))).not.toContain("Scan default");
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

  it("says each column's best value in a word, a tie on both models, and nothing on a lone value", () => {
    const fast = board("a/fast", { seconds_per_page_p50: 2, scan_cost_usd: 8, runs_failed: 0 });
    const cheap = board("b/cheap", { seconds_per_page_p50: 4.2, scan_cost_usd: 2, seconds_per_page_p95: null });
    const html = boardHtml(ready(boardData({ items: [fast, cheap] })));
    const row = (model: string) => html.split("<tr").find((r) => r.includes(model)) ?? "";
    // The word follows its value after a space, so a screen reader says "typical 2 s fastest".
    const tagged = (model: string, value: string, word: string) =>
      expect(row(model)).toMatch(new RegExp(`${value.replace(/[$.]/g, "\\$&")} <span class="[^"]*best[^"]*">${word}</span>`));
    tagged("a/fast", "typical 2 s", "fastest");
    tagged("a/fast", "0.0% failed", "fewest");
    tagged("b/cheap", "$5.00", "lowest");
    expect(row("a/fast")).not.toContain("$20.00 <span");
    expect(row("b/cheap")).not.toMatch(/>(fastest|fewest)</);
    // The same AI cost on both is a tie: both say lowest.
    tagged("a/fast", "$6.50", "lowest");
    tagged("b/cheap", "$6.50", "lowest");
    // One model alone has nothing to beat.
    expect(boardHtml(ready(boardData({ items: [fast] })))).not.toMatch(/class="[^"]*best/);
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
