"use client";

// Overview, the first tab of /operator (operator insights, council 11 Oct 2026, F1 F2):
// the period's figures for the whole platform, what needs attention now, and each
// organisation at a glance. Counts, sums, rates and org names only: never a person or a
// document (the server sends neither). Loads once per period and on Refresh, never on a
// timer; after 15 minutes it says the figures are old. OverviewScreen is the view,
// rendered in node by tests/operatorInsightsViews.test.ts.

import { useEffect, useRef, useState } from "react";
import { useMinuteNow } from "@/components/chat/hooks";
import table from "@/components/documents/Table.module.css";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { getHealth, getOperatorInsights, getOperatorQuality, listOrgs } from "@/lib/api";
import { formatCost, formatDateTime, formatNumber, formatUsd } from "@/lib/format";
import type { InsightsReply, OrgInsight, Organisation, PlatformHealth, QualityReply } from "@/lib/types";
import { Sparkline } from "./Charts";
import {
  ATTENTION,
  CHANGE_MIN_BASE,
  INSIGHT_PERIODS,
  attentionItems,
  changeText,
  csvOf,
  dayList,
  monthForecast,
  orgFlags,
  periodTotals,
  riskOrder,
  usdPer1000Pages,
  usdPer100Questions,
} from "./insights";
import type { AttentionItem, Flag, InsightPeriod, MonthForecast, PeriodTotals, Severity } from "./insights";
import { LoadFailed } from "./LoadFailed";
import { operatorErrorMessage } from "./logic";
import { usd } from "./models";
import { waitWords } from "./platform";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";
import css from "./Insights.module.css";

const STALE_MS = 15 * 60_000;
const ATTENTION_SHOWN = 5;
const SKELETON_TILES = [0, 1, 2, 3];
export const AI_OFF = "AI is off on this server";

/** ready with refreshing: the last figures, shown while Refresh reloads the same period. */
export type LoadState<T> = { kind: "loading" } | { kind: "error"; message: string } | { kind: "ready"; data: T; refreshing?: boolean };

export interface Loaded<T> {
  days: InsightPeriod;
  attempt: number;
  state: LoadState<T>;
}

/**
 * What a screen shows for (days, attempt): its own result once it came; while a new
 * attempt reloads the same period, the last ready figures marked refreshing (so the
 * buttons, sort and focus stay); otherwise loading (the first load, a new period, or
 * Try again after an error). A reply for an older period or attempt is never shown.
 */
export function shownState<T>(loaded: Loaded<T> | null, days: InsightPeriod, attempt: number): LoadState<T> {
  if (loaded === null || loaded.days !== days) return { kind: "loading" };
  if (loaded.attempt === attempt) return loaded.state;
  return loaded.state.kind === "ready" ? { ...loaded.state, refreshing: true } : { kind: "loading" };
}

/** Loads once per period and per attempt (Refresh, Try again), never on a timer. `load` is a module-level function. */
export function useLoad<T>(days: InsightPeriod, attempt: number, load: (days: InsightPeriod) => Promise<T>): LoadState<T> {
  const [loaded, setLoaded] = useState<Loaded<T> | null>(null);
  useEffect(() => {
    let cancelled = false;
    load(days).then(
      (data) => !cancelled && setLoaded({ days, attempt, state: { kind: "ready", data } }),
      (caught: unknown) => !cancelled && setLoaded({ days, attempt, state: { kind: "error", message: operatorErrorMessage(caught) } }),
    );
    return () => {
      cancelled = true;
    };
  }, [days, attempt, load]);
  return shownState(loaded, days, attempt);
}

export function PeriodSelect({ id, days, onDays }: { id: string; days: InsightPeriod; onDays(days: InsightPeriod): void }) {
  return (
    <div className="field">
      <label htmlFor={id}>Period</label>
      <select
        id={id}
        className="input"
        value={days}
        onChange={(event) => {
          const chosen = INSIGHT_PERIODS.find((option) => String(option) === event.target.value);
          if (chosen) onDays(chosen);
        }}
      >
        {INSIGHT_PERIODS.map((option) => (
          <option key={option} value={option}>
            Last {option} days
          </option>
        ))}
      </select>
    </div>
  );
}

export function Skeleton() {
  return (
    <div className={`delayed ${admin.tiles}`} aria-hidden="true">
      {SKELETON_TILES.map((tile) => (
        <span key={tile} className={`skeleton ${admin.skeletonTile}`} />
      ))}
    </div>
  );
}

/** "Act now" or "Check": an icon and the words, never colour alone. */
export function SeverityMark({ severity }: { severity: Severity }) {
  return (
    <span className={css.severity} data-severity={severity}>
      <Icon name={severity === "act" ? "alert" : "eye-alert"} size={16} />
      {severity === "act" ? "Act now" : "Check"}
    </span>
  );
}

export interface OverviewData {
  reply: InsightsReply;
  orgs: Organisation[];
  quality: QualityReply | null; // not loaded: Needs attention says it may be incomplete
  health: PlatformHealth | null;
  loadedAt: number; // Date.now() when it arrived: the flags are judged then
}

export interface OverviewProps {
  openOrg(name: string): void;
  openTab(tab: "health" | "models"): void;
}

async function loadOverview(days: InsightPeriod): Promise<OverviewData> {
  // Quality and health only add attention items: the overview still shows without them.
  const [reply, orgs, quality, health] = await Promise.all([
    getOperatorInsights(days),
    listOrgs(),
    getOperatorQuality(days).catch(() => null),
    getHealth().catch(() => null),
  ]);
  return { reply, orgs: orgs.items, quality, health, loadedAt: Date.now() };
}

export function OverviewView(props: OverviewProps) {
  const [days, setDays] = useState<InsightPeriod>(30);
  const [attempt, setAttempt] = useState(0);
  const now = useMinuteNow();
  const state = useLoad(days, attempt, loadOverview);
  return (
    <OverviewScreen {...props} days={days} onDays={setDays} state={state} onRefresh={() => setAttempt((n) => n + 1)} now={now} />
  );
}

export function OverviewScreen(
  props: OverviewProps & {
    days: InsightPeriod;
    onDays(days: InsightPeriod): void;
    state: LoadState<OverviewData>;
    onRefresh(): void;
    now: number; // the minute clock; 0 on the server
  },
) {
  const { days, onDays, state, onRefresh, now } = props;
  const heading = useRef<HTMLHeadingElement>(null);
  // The sort, said once it changes; only for the figures it was said on, so a reload clears it.
  const [said, setSaid] = useState({ text: "", at: 0 });
  const data = state.kind === "ready" ? state.data : null;
  const refreshing = state.kind === "ready" && state.refreshing === true;
  const empty = data !== null && data.orgs.length === 0 && data.reply.organisations.length === 0;
  const status =
    state.kind === "loading" ? "Loading the overview\u2026" : refreshing ? "Refreshing\u2026" : said.at === data?.loadedAt ? said.text : "";
  return (
    <section className={admin.section} aria-labelledby="overview-heading">
      <h2 id="overview-heading" ref={heading} tabIndex={-1} className={admin.sectionHeading}>
        Overview
      </h2>
      <p className={styles.summary}>Counts, sums and rates per organisation. No person or document is shown here.</p>
      <div className={styles.toolbar}>
        <PeriodSelect id="overview-period" days={days} onDays={onDays} />
        {data && (
          <div className={css.toolEnd}>
            <p className={css.asOf}>
              Figures as of {formatDateTime(new Date(data.loadedAt).toISOString())}
              {refreshing && ", refreshing\u2026"}
            </p>
            <button type="button" className="btn" onClick={onRefresh}>
              <Icon name="refresh" />
              Refresh
            </button>
            {!empty && (
              <button type="button" className="btn" onClick={() => downloadCsv(data)}>
                Download CSV
              </button>
            )}
          </div>
        )}
      </div>
      {data && now > 0 && now - data.loadedAt >= STALE_MS && (
        <p className={css.stale}>
          <Icon name="history" />
          These figures are over 15 minutes old. Refresh to see new ones.
        </p>
      )}
      <p className="visually-hidden" role="status">
        {status}
      </p>
      {state.kind === "loading" && <Skeleton />}
      {state.kind === "error" && (
        <LoadFailed
          what="the overview"
          message={state.message}
          onRetry={() => {
            heading.current?.focus(); // so focus stays on this screen, not the page, when the banner goes
            onRefresh();
          }}
        />
      )}
      {empty && (
        <EmptyState icon="admin" title="No organisations yet">
          Create the first one in Organisations; its figures show here once it is used.
        </EmptyState>
      )}
      {data && !empty && <OverviewBody {...props} data={data} announce={(text) => setSaid({ text, at: data.loadedAt })} />}
    </section>
  );
}

function OverviewBody({ data, openOrg, openTab, announce }: OverviewProps & { data: OverviewData; announce(text: string): void }) {
  const { reply } = data;
  const names = new Map(data.orgs.map((org) => [org.id, org.name]));
  const items = riskOrder(attentionItems(reply, names, data.quality, data.health, new Date(data.loadedAt)));
  return (
    <>
      <Attention items={items} openOrg={openOrg} openTab={openTab} />
      <h3 className={admin.tilesHeading}>The last {dayList(reply.from, reply.to).length} days</h3>
      {!reply.ai_enabled && <p className={admin.emptyLine}>{AI_OFF}, so nothing is spent and no unit cost is shown.</p>}
      <dl className={admin.tiles}>
        {tilesOf(reply).map((tile) => (
          <div key={tile.label} className={admin.tile}>
            <dt className={admin.tileLabel}>{tile.label}</dt>
            <dd className={css.tileBody}>
              <span className={`${admin.tileValue} tabular`}>{tile.value}</span>
              {tile.note && <span className={css.note}>{tile.note}</span>}
              {tile.change && <span className={css.note}>{tile.change}</span>}
              {tile.spark && (
                <span className={css.spark}>
                  <Sparkline values={tile.spark} />
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      <p className={css.footnote}>Each change compares with the same number of days before. Money is in US dollars.</p>
      <OrgTable data={data} names={names} announce={announce} />
    </>
  );
}

function Attention({ items, openOrg, openTab }: OverviewProps & { items: AttentionItem[] }) {
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? items : items.slice(0, ATTENTION_SHOWN);
  return (
    <>
      <h3 className={admin.tilesHeading}>Needs attention</h3>
      {items.length === 0 ? (
        <p className={admin.emptyLine}>Nothing needs attention now.</p>
      ) : (
        <ul className={css.attention}>
          {shown.map((item, index) => (
            <li key={`${index}|${item.text}`} className={css.attentionItem}>
              <SeverityMark severity={item.severity} />
              <span className={css.attentionText}>{item.text}</span>
              {item.target === "organisation" && item.organisationName && (
                <button
                  type="button"
                  className="btn"
                  onClick={() => openOrg(item.organisationName ?? "")}
                  aria-label={`Open ${item.organisationName} in Organisations`}
                >
                  Open
                </button>
              )}
              {item.target !== "organisation" && (
                <button type="button" className="btn" onClick={() => openTab(item.target === "health" ? "health" : "models")}>
                  Open {item.target === "health" ? "Health" : "Models"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {items.length > ATTENTION_SHOWN && (
        <p>
          <button type="button" className="btn" aria-expanded={showAll} onClick={() => setShowAll((open) => !open)}>
            {showAll ? "Show fewer" : `Show all ${formatNumber(items.length)}`}
          </button>
        </p>
      )}
    </>
  );
}

interface Tile {
  label: string;
  value: string;
  note?: string;
  change?: string | null;
  spark?: number[];
}

/** The 8 tiles: the period's totals, each with its change on the period before and a line by day. */
function tilesOf(reply: InsightsReply): Tile[] {
  const days = dayList(reply.from, reply.to);
  const all = dayList(reply.previous_from, reply.to);
  const now = periodTotals(reply.daily, reply.from, reply.to);
  const before = periodTotals(reply.daily, reply.previous_from, all[all.length - days.length - 1] ?? reply.previous_from);
  const perDay = days.map((day) => periodTotals(reply.daily, day, day));
  const line = (pick: (totals: PeriodTotals) => number | null) => perDay.map((totals) => pick(totals) ?? 0);
  const count = (label: string, pick: (totals: PeriodTotals) => number, note?: string): Tile => ({
    label,
    value: formatNumber(pick(now)),
    note,
    change: changeText(pick(now), pick(before), "count"),
    spark: line(pick),
  });
  // A unit cost's change is judged on its pages or questions before (`base`), never on its dollars.
  const money = (label: string, pick: (totals: PeriodTotals) => number | null, note: string, base?: (totals: PeriodTotals) => number): Tile => {
    if (!reply.ai_enabled) return { label, value: "Off", note: AI_OFF };
    const value = pick(now);
    const was = pick(before);
    let change: string | null = null;
    if (value !== null && base && base(before) < CHANGE_MIN_BASE) change = "too few to compare";
    else if (value !== null && was !== null) change = changeText(value, was, "money");
    return { label, value: usd(value, now.costEstimated), note, change, spark: line(pick) };
  };
  const month = monthSpend(reply.organisations);
  const forecast = monthForecast(month.cost_usd, null, reply.month);
  const spend: Tile = reply.ai_enabled
    ? {
        ...money("AI spend", (totals) => totals.costUsd, ""),
        value: formatCost({ cost_usd: now.costUsd, cost_estimated: now.costEstimated, cost_part_unknown: now.costPartUnknown }),
        note: `Month-end forecast ${formatCost({ ...month, cost_usd: forecast.usd })}`,
      }
    : money("AI spend", (totals) => totals.costUsd, "");
  return [
    count("Active organisations", (totals) => totals.activeOrganisations),
    count("Files uploaded", (totals) => totals.uploads),
    count("Pages processed", (totals) => totals.pages, "(re-reads included)"),
    count("Questions asked", (totals) => totals.questions),
    spend,
    money("$ per 1,000 pages", (totals) => usdPer1000Pages(totals.costDocumentsUsd, totals.pages), "Reading and document cards", (totals) => totals.pages),
    money("$ per 100 questions", (totals) => usdPer100Questions(totals.costQuestionsUsd, totals.questions), "Answers, memory and search", (totals) => totals.questions),
    count("Failed runs", (totals) => totals.runsFailed, `of ${formatNumber(now.runs)} runs`),
  ];
}

/** This calendar month's spend over every organisation, as formatCost reads it. */
function monthSpend(orgs: readonly OrgInsight[]) {
  const known = orgs.filter((org) => org.month_cost_usd !== null);
  return {
    cost_usd: known.length === 0 ? null : known.reduce((sum, org) => sum + (org.month_cost_usd ?? 0), 0),
    cost_estimated: orgs.some((org) => org.month_cost_estimated),
    cost_part_unknown: orgs.some((org) => org.month_cost_part_unknown) || (known.length > 0 && known.length < orgs.length),
  };
}

// --- Organisations at a glance ------------------------------------------------------

interface OrgLine {
  org: OrgInsight;
  name: string;
  flags: Flag[];
  forecast: MonthForecast;
}

const UNKNOWN_ORG = "Unknown organisation";

function orgLines(data: OverviewData, names: ReadonlyMap<string, string>): OrgLine[] {
  const at = new Date(data.loadedAt);
  return data.reply.organisations.map((org) => ({
    org,
    name: names.get(org.organisation_id) ?? UNKNOWN_ORG,
    flags: riskOrder(orgFlags(org, data.reply, at)),
    forecast: monthForecast(org.month_cost_usd, org.ai_budget_usd, data.reply.month),
  }));
}

type Column = "name" | "activity" | "spend" | "review" | "flags";
const rank = (line: OrgLine) => (line.flags[0]?.severity === "act" ? 0 : line.flags[0] ? 1 : 2);
interface Sort {
  id: string;
  label: string;
  column: Column; // the column that says aria-sort
  dir: "ascending" | "descending";
  compare(a: OrgLine, b: OrgLine): number; // ties go by name
}
const RISK_SORT: Sort = { id: "risk", label: "Most at risk first", column: "flags", dir: "descending", compare: (a, b) => rank(a) - rank(b) };
const SORTS: Sort[] = [
  RISK_SORT,
  { id: "name", label: "Name, A to Z", column: "name", dir: "ascending", compare: () => 0 },
  { id: "activity", label: "Least active first", column: "activity", dir: "ascending", compare: (a, b) => a.org.active_users_30d - b.org.active_users_30d },
  { id: "spend", label: "Most AI spend first", column: "spend", dir: "descending", compare: (a, b) => (b.org.month_cost_usd ?? -1) - (a.org.month_cost_usd ?? -1) },
  { id: "review", label: "Most waiting for review first", column: "review", dir: "descending", compare: (a, b) => b.org.open_review - a.org.open_review },
];

const COLUMNS: { id: Column; label: string }[] = [
  { id: "name", label: "Organisation" },
  { id: "activity", label: "Active users" },
  { id: "spend", label: "AI spend this month" },
  { id: "review", label: "Waiting for review" },
  { id: "flags", label: "Flags" },
];

function OrgTable({ data, names, announce }: { data: OverviewData; names: ReadonlyMap<string, string>; announce(text: string): void }) {
  const [sort, setSort] = useState(RISK_SORT);
  const lines = orgLines(data, names).sort((a, b) => sort.compare(a, b) || a.name.localeCompare(b.name));
  const { reply } = data;
  return (
    <>
      <div className={styles.toolbar}>
        <h3 className={admin.tilesHeading}>Organisations at a glance</h3>
        <div className="field">
          <label htmlFor="overview-sort">Sort by</label>
          <select
            id="overview-sort"
            className="input"
            value={sort.id}
            onChange={(event) => {
              const chosen = SORTS.find((option) => option.id === event.target.value);
              if (!chosen) return;
              setSort(chosen);
              announce(`Sorted: ${chosen.label.toLocaleLowerCase()}.`);
            }}
          >
            {SORTS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {lines.length === 0 ? (
        <p className={admin.emptyLine}>No organisation was used in this period.</p>
      ) : (
        <div className={table.reflowWrap}>
          <table className={`table ${table.reflow}`} role="table">
            <caption className={admin.caption}>Organisations at a glance, {sort.label.toLocaleLowerCase()}</caption>
            <thead role="rowgroup">
              <tr role="row">
                {COLUMNS.map((column) => (
                  <th
                    key={column.id}
                    scope="col"
                    role="columnheader"
                    aria-sort={column.id === sort.column ? sort.dir : undefined}
                    className={column.id === "spend" ? "num" : undefined}
                  >
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody role="rowgroup">
              {lines.map((line) => (
                <OrgGlance key={line.org.organisation_id} line={line} aiOn={reply.ai_enabled} at={data.loadedAt} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function OrgGlance({ line, aiOn, at }: { line: OrgLine; aiOn: boolean; at: number }) {
  const { org, name, flags, forecast } = line;
  const cost = { cost_usd: org.month_cost_usd, cost_estimated: org.month_cost_estimated, cost_part_unknown: org.month_cost_part_unknown };
  const budget = org.ai_budget_usd;
  const spent = org.month_cost_usd ?? 0;
  const oldest = org.oldest_open_review_at ? Date.parse(org.oldest_open_review_at) : NaN;
  return (
    <tr role="row">
      <td role="cell" className={styles.orgName}>
        {name}
      </td>
      <td role="cell" data-label="Active users" className="tabular">
        {formatNumber(org.active_users_7d)} in 7 days
        <span className={styles.statusNote}>{formatNumber(org.active_users_30d)} in 30 days</span>
      </td>
      <td role="cell" data-label="AI spend this month" className="num">
        {!aiOn ? (
          AI_OFF
        ) : budget === null ? (
          <>
            {formatCost(cost)}
            <span className={styles.statusNote}>No budget</span>
          </>
        ) : (
          <span className={css.budget}>
            {org.month_cost_usd !== null && (
              <meter
                min={0}
                max={Math.max(budget, spent)}
                low={budget * ATTENTION.budgetCheck}
                high={budget}
                optimum={0}
                value={spent}
                aria-label={`Budget used by ${name}`}
              />
            )}
            <span>
              {formatCost(cost)} of {formatUsd(budget)}
            </span>
          </span>
        )}
        {aiOn && <span className={styles.statusNote}>Month-end forecast {usd(forecast.usd, org.month_cost_estimated)}</span>}
        {aiOn && forecast.passDay !== null && (
          <span className={styles.statusNote}>Passes the budget around day {forecast.passDay}</span>
        )}
      </td>
      <td role="cell" data-label="Waiting for review" className="tabular">
        {formatNumber(org.open_review)}
        {Number.isFinite(oldest) && <span className={styles.statusNote}>oldest {waitWords(Math.max(0, (at - oldest) / 1000))}</span>}
      </td>
      <td role="cell" data-label="Flags">
        {flags.length === 0 ? (
          <span className="muted">None</span>
        ) : (
          <ul className={css.flags}>
            {flags.map((flag) => (
              <li key={flag.code}>
                <SeverityMark severity={flag.severity} /> {flag.text}
              </li>
            ))}
          </ul>
        )}
      </td>
    </tr>
  );
}

// --- Download CSV: the same rows, made in the browser ------------------------------------

export function overviewCsv(data: OverviewData): string {
  const names = new Map(data.orgs.map((org) => [org.id, org.name]));
  const lines = orgLines(data, names).sort((a, b) => a.name.localeCompare(b.name));
  return csvOf([
    [
      "Organisation", "Active users 7 days", "Active users 30 days", "Answers 30 days", "Not found 30 days",
      "Waiting for review", "AI spend this month (USD)", "Cost estimated", "Cost part not known", "Budget (USD)",
      "Month-end forecast (USD)", "Flags",
    ],
    ...lines.map(({ org, name, flags, forecast }) => [
      name, org.active_users_7d, org.active_users_30d, org.answers_30d, org.not_found_30d, org.open_review,
      org.month_cost_usd, org.month_cost_estimated ? "yes" : "no", org.month_cost_part_unknown ? "yes" : "no",
      org.ai_budget_usd, forecast.usd, flags.map((flag) => `${flag.severity === "act" ? "Act now" : "Check"}: ${flag.text}`).join("; "),
    ]),
  ]);
}

export function downloadCsv(data: OverviewData) {
  const url = URL.createObjectURL(new Blob([overviewCsv(data)], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `kaagaz-overview-${data.reply.from}-to-${data.reply.to}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0); // after the click has started the download
}
