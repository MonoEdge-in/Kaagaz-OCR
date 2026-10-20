"use client";

// The organisation's AI use (model choice SP2, SP4): pick a period (Today,
// Last 7 days, This month, or two dates) and, if wanted, one person; then the
// tokens used with the period and a day's average, the tokens by day (upright bars
// drawn with CSS, no chart library; a hidden table reads the same numbers out), by
// person, kind of work (with each kind's share) and the top documents. Org admins and their
// users never see money, spend or the budget: those stay in the super admin's console
// (Tushar, 15 Oct 2026). Models stay the operator's only.
// Keyless mode says AI is off rather than "no use".
// The view is UsageReportView, rendered in node by tests/admin-usage.test.ts.

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import type { ReactNode } from "react";
import table from "@/components/documents/Table.module.css";
import { Icon } from "@/components/ui/Icon";
import { ApiError, getAdminUsage, listUsers } from "@/lib/api";
import { formatDate, formatDateTime, formatNumber } from "@/lib/format";
import type { AdminUser, UsageReport, UsageTokens } from "@/lib/types";
import {
  USAGE_PERIODS,
  usageKindText,
  usageRange,
  usageState,
} from "./logic";
import type { UsagePeriod, UsageState } from "./logic";
import styles from "./Admin.module.css";

const SKELETON_TILES = [0];
// A by-user row with no user: work no user asked for (the sweeps), or a removed user's.
export const REMOVED_OR_SYSTEM = "Removed user or system work";

const DAY_MS = 24 * 60 * 60 * 1000;
// The report's days are dates without a time, so they are read and shown in UTC.
const compactFormat = new Intl.NumberFormat("en-US", { notation: "compact", maximumSignificantDigits: 3 });
const rangeFormat = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const tickFormat = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
const dayDate = (day: string) => new Date(`${day}T00:00:00Z`);

/** 4200000 -> "4.2M", 280000 -> "280K", 950 -> "950". */
export function compactNumber(value: number): string {
  return compactFormat.format(value);
}

/** "1–15 Oct 2026"; "28 Sept – 15 Oct 2026" across months; one date for one day. */
export function periodText(from: string, to: string): string {
  return rangeFormat.formatRange(dayDate(from), dayDate(to));
}

/** Every day from `from` to `to`, both counted, as YYYY-MM-DD. */
export function periodDays(from: string, to: string): string[] {
  const days: string[] = [];
  for (let time = dayDate(from).getTime(); time <= dayDate(to).getTime(); time += DAY_MS) {
    days.push(new Date(time).toISOString().slice(0, 10));
  }
  return days;
}

/** A row's share of all the tokens, in words: "67%", "Under 1%" for a sliver. */
export function shareText(part: number, all: number): string {
  if (all <= 0 || part <= 0) return "0%";
  const percent = Math.round((part / all) * 100);
  return percent === 0 ? "Under 1%" : `${percent}%`;
}

/** The days of the report with any AI use. */
export function activeDays(report: Pick<UsageReport, "by_day">): number {
  return report.by_day.filter((day) => usageTokens(day) > 0).length;
}

/**
 * AD5 (UI audit, 16 Oct 2026): the average over the days with use, not every day of the
 * period, so one busy day is not spread thin: "Average per active day: 75K (2 of 11 days)".
 */
export function averageText(total: number, active: number, days: number): string {
  return `Average per active day: ${compactNumber(Math.round(total / active))} (${active} of ${days} days)`;
}

/** All tokens of some calls: output_tokens already counts the thinking (reasoning_tokens). */
export function usageTokens(totals: UsageTokens): number {
  return totals.input_tokens + totals.output_tokens;
}

interface Row {
  key: string;
  name: ReactNode;
  totals: UsageTokens;
}

/**
 * One table of rows: a name, then the tokens, the most first (the server sorts by
 * cost). The shared reflow (Table.module.css): cards below 48rem.
 */
function UsageTable({
  caption,
  first,
  rows,
  share = false,
}: {
  caption: string;
  first: string;
  rows: Row[];
  /** Adds a Share column: each row's part of the table's tokens, in words and a bar. */
  share?: boolean;
}) {
  const sorted = [...rows].sort((a, b) => usageTokens(b.totals) - usageTokens(a.totals));
  const all = rows.reduce((sum, row) => sum + usageTokens(row.totals), 0);
  return (
    <div className={`${table.reflowWrap} ${styles.tableScroll}`}>
      <table className={`table ${table.reflow}`} role="table">
        <caption className={styles.caption}>{caption}</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader">
              {first}
            </th>
            <th scope="col" role="columnheader" className="num">
              Tokens
            </th>
            {share && (
              <th scope="col" role="columnheader">
                Share
              </th>
            )}
          </tr>
        </thead>
        <tbody role="rowgroup">
          {sorted.map((row) => (
            <tr key={row.key} role="row">
              <td role="cell" data-label={first} className={`${styles.strong} ${styles.wrap}`}>
                {row.name}
              </td>
              <td role="cell" data-label="Tokens" className="num">
                {formatNumber(usageTokens(row.totals))}
              </td>
              {share && (
                <td role="cell" data-label="Share">
                  <span className={styles.barCell}>
                    <span>{shareText(usageTokens(row.totals), all)}</span>
                    {/* All of the tokens would be a 5rem bar. */}
                    <span
                      className={styles.bar}
                      aria-hidden="true"
                      style={{ width: `${all > 0 ? (usageTokens(row.totals) / all) * 5 : 0}rem` }}
                    />
                  </span>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Each day's tokens as upright bars, one per day of the period (days with no use
 * stay empty), the busiest day named over its bar and a few dates under them. The
 * drawing is hidden from screen readers; the table under it (visually hidden) reads
 * the same numbers out.
 */
function DailyChart({ report }: { report: UsageReport }) {
  const used = new Map(report.by_day.map((day) => [day.day, usageTokens(day)]));
  const days = periodDays(report.from, report.to).map((day) => ({ day, tokens: used.get(day) ?? 0 }));
  const most = Math.max(0, ...days.map((day) => day.tokens));
  const peak = days.find((day) => day.tokens === most);
  const last = days.length - 1;
  // A date under the first and the last bar, and every week between (every few weeks
  // for a long period, so about six fit a phone), leaving out one that would crowd the last.
  const step = 7 * Math.ceil(days.length / 42);
  const ticks = days.filter(
    (_, index) => index === 0 || index === last || (index % step === 0 && last - index >= step / 2),
  );
  // left: x% with translateX(-x%) keeps a label inside the box at both ends.
  const at = (day: string) => {
    const x = last > 0 ? (days.findIndex((d) => d.day === day) / last) * 100 : 50;
    return { left: `${x}%`, transform: `translateX(-${x}%)` };
  };
  return (
    <div className={styles.chartCard}>
      <div className={styles.chartHead} aria-hidden="true">
        <span className={styles.chartTitle}>Tokens by day</span>
        <span className={styles.chartNote}>Each bar is one day</span>
      </div>
      <div className={styles.chart} aria-hidden="true">
        <div className={styles.plot}>
          {most > 0 && peak && (
            <span className={styles.peak} style={at(peak.day)}>
              {compactNumber(most)} · {tickFormat.format(dayDate(peak.day))}
            </span>
          )}
          {days.map(({ day, tokens }) => (
            <span
              key={day}
              className={styles.dayBar}
              style={{ height: tokens > 0 ? `max(2px, ${(tokens / most) * 100}%)` : 0 }}
            />
          ))}
        </div>
        <div className={styles.ticks}>
          {ticks.map(({ day }) => (
            <span key={day} className={styles.tick} style={at(day)}>
              {tickFormat.format(dayDate(day))}
            </span>
          ))}
        </div>
      </div>
      <div className="visually-hidden">
        <DailyTable report={report} />
      </div>
    </div>
  );
}

/** Each day's tokens, every day of the period with 0 for none, for screen readers (DailyChart draws them). */
function DailyTable({ report }: { report: UsageReport }) {
  const used = new Map(report.by_day.map((day) => [day.day, usageTokens(day)]));
  return (
    <table className="table" role="table">
      <caption>Tokens by day</caption>
      <thead role="rowgroup">
        <tr role="row">
          <th scope="col" role="columnheader">
            Day
          </th>
          <th scope="col" role="columnheader" className="num">
            Tokens
          </th>
        </tr>
      </thead>
      <tbody role="rowgroup">
        {periodDays(report.from, report.to).map((day) => (
          <tr key={day} role="row">
            <td role="cell">{formatDate(`${day}T12:00:00+05:30`)}</td>
            <td role="cell" data-label="Tokens" className="num">
              {formatNumber(used.get(day) ?? 0)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function UsageReportView({ state, onRetry }: { state: UsageState; onRetry: () => void }) {
  if (state.kind === "error") {
    return (
      <div className={styles.errorBanner} role="alert">
        <Icon name="alert" className={styles.errorIcon} />
        <div className={styles.errorBody}>
          <p className={styles.errorTitle}>Could not load the AI use.</p>
          <p>{state.message}</p>
        </div>
        <button type="button" className="btn" onClick={onRetry}>
          <Icon name="refresh" />
          Retry
        </button>
      </div>
    );
  }
  if (state.kind === "loading") {
    return (
      <>
        <p className="visually-hidden" role="status">
          Loading the AI use…
        </p>
        <div className={`delayed ${styles.tiles}`} aria-hidden="true">
          {SKELETON_TILES.map((tile) => (
            <span key={tile} className={`skeleton ${styles.skeletonTile}`} />
          ))}
        </div>
      </>
    );
  }
  if (state.kind === "off") {
    return <p className={styles.emptyLine}>AI is off on this server, so no tokens are used.</p>;
  }
  if (state.kind === "empty") return <p className={styles.emptyLine}>No AI use in this period.</p>;

  const { report } = state;
  const total = usageTokens(report.totals);
  const days = periodDays(report.from, report.to).length;
  const active = activeDays(report);
  return (
    <>
      <dl className={styles.tiles}>
        <div className={`${styles.tile} ${styles.usageTile}`}>
          <dt className={styles.usageTileHead}>
            <span>Tokens in this period</span>
            <span className="visually-hidden">, </span>
            <span className={styles.usageRange}>{periodText(report.from, report.to)}</span>
          </dt>
          <dd className={`${styles.tileValue} tabular`} title={formatNumber(total)}>
            {compactNumber(total)} <span className={styles.tileUnit}>tokens</span>
          </dd>
          {days > 1 && active > 0 && (
            <dd className={styles.tileNote}>
              {averageText(total, active, days)}
            </dd>
          )}
        </div>
      </dl>
      <div className={styles.usageTables}>
        <DailyChart report={report} />
        <UsageTable
          caption="By user"
          first="Person"
          rows={report.by_user.map((row) => ({
            key: row.user_id ?? "none",
            name: row.user_name ?? REMOVED_OR_SYSTEM,
            totals: row,
          }))}
        />
        <UsageTable
          caption="By kind of work"
          first="Kind"
          rows={report.by_kind.map((row) => ({ key: row.kind, name: usageKindText(row.kind), totals: row }))}
          share
        />
        <UsageTable
          caption="Top documents"
          first="Document"
          rows={report.top_documents.map((row, index) => ({
            key: row.document_id ?? `deleted-${index}`,
            name: row.document_id === null ? row.name : <Link href={`/documents/${row.document_id}`}>{row.name}</Link>,
            totals: row,
          }))}
        />
      </div>
      {/* No CSV here: it has money and model names, which are the super admin's only. */}
    </>
  );
}

export function UsagePanel() {
  const id = useId();
  const [now] = useState(() => Date.now());
  const [period, setPeriod] = useState<UsagePeriod>("month");
  const [custom, setCustom] = useState({ from: "", to: "" });
  const [userId, setUserId] = useState("");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{
    key: string;
    report: UsageReport | null;
    error: string | null;
    at: number; // when it arrived: "Figures as of", a real time
  } | null>(null);

  const range = usageRange(period, custom, now);
  const from = range?.from;
  const to = range?.to;
  const key = `${attempt} ${from} ${to} ${userId}`;

  useEffect(() => {
    let cancelled = false;
    // Not loaded: the select offers Everyone only; the report still loads.
    listUsers().then(
      (reply) => {
        if (!cancelled) setUsers(reply.items);
      },
      () => undefined,
    );
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (from === undefined || to === undefined) return;
    let cancelled = false;
    getAdminUsage({ from, to, user_id: userId || undefined }).then(
      (report) => {
        if (!cancelled) setResult({ key, report, error: null, at: Date.now() });
      },
      (caught: unknown) => {
        if (!cancelled) {
          const message =
            caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.";
          setResult({ key, report: null, error: message, at: Date.now() });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [key, from, to, userId]);

  const current = result !== null && result.key === key ? result : null;
  const state = usageState(current?.report ?? null, current?.error ?? null);

  return (
    <section className={styles.section} aria-labelledby="usage-heading">
      <h2 id="usage-heading" className={styles.sectionHeading}>
        Usage
      </h2>
      <div className={styles.usageControls}>
        <div className="field">
          <label className="label" htmlFor={`${id}-period`}>
            Period
          </label>
          <select
            id={`${id}-period`}
            className="input"
            value={period}
            onChange={(event) => setPeriod(event.target.value as UsagePeriod)}
          >
            {USAGE_PERIODS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        {period === "custom" && (
          <>
            <div className="field">
              <label className="label" htmlFor={`${id}-from`}>
                From
              </label>
              <input
                id={`${id}-from`}
                type="date"
                className="input"
                value={custom.from}
                max={custom.to || undefined}
                onChange={(event) => setCustom({ ...custom, from: event.target.value })}
              />
            </div>
            <div className="field">
              <label className="label" htmlFor={`${id}-to`}>
                To
              </label>
              <input
                id={`${id}-to`}
                type="date"
                className="input"
                value={custom.to}
                min={custom.from || undefined}
                onChange={(event) => setCustom({ ...custom, to: event.target.value })}
              />
            </div>
          </>
        )}
        <div className="field">
          <label className="label" htmlFor={`${id}-user`}>
            Person
          </label>
          <select id={`${id}-user`} className="input" value={userId} onChange={(event) => setUserId(event.target.value)}>
            <option value="">Everyone</option>
            {users.map((user) => (
              <option key={user.id} value={user.id}>
                {user.display_name}
              </option>
            ))}
          </select>
        </div>
        {current?.report && (
          <div className={styles.usageFresh}>
            <p className={styles.asOf}>Figures as of {formatDateTime(new Date(current.at).toISOString())}</p>
            <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
              <Icon name="refresh" />
              Refresh
            </button>
          </div>
        )}
      </div>
      {range ? (
        <UsageReportView state={state} onRetry={() => setAttempt((n) => n + 1)} />
      ) : (
        <p className="hint">Pick a start date and an end date, the start first.</p>
      )}
    </section>
  );
}
