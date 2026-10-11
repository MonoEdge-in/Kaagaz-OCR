"use client";

// The organisation's AI use (model choice SP2, SP4): pick a period (Today,
// Last 7 days, This month, or two dates) and, if wanted, one user; then the
// spend, the tokens and how much of this month's budget is used, with a line
// at 80% and at 100% (warn only: nothing stops). Then tables by user, model,
// kind of work and the costliest documents, a daily table whose bars are
// drawn with CSS (the number is in the cell; no chart library), and the same
// rows as a CSV download. Keyless mode says AI is off rather than "no use".
// The view is UsageReportView, rendered in node by tests/admin-usage.test.ts.

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import type { ReactNode } from "react";
import table from "@/components/documents/Table.module.css";
import { Icon } from "@/components/ui/Icon";
import { ApiError, adminUsageCsvUrl, getAdminUsage, listUsers } from "@/lib/api";
import type { UsageParams } from "@/lib/api";
import { formatCost, formatDate, formatNumber } from "@/lib/format";
import type { AdminUser, UsageReport, UsageTotals } from "@/lib/types";
import {
  USAGE_PERIODS,
  budgetLine,
  budgetShareText,
  budgetWarning,
  usageKindText,
  usageRange,
  usageState,
} from "./logic";
import type { UsagePeriod, UsageState } from "./logic";
import styles from "./Admin.module.css";

const SKELETON_TILES = [0, 1, 2];

interface Row {
  key: string;
  name: ReactNode;
  totals: UsageTotals;
}

const AMOUNTS = ["Tokens in", "Tokens out", "Cost"] as const;

/**
 * One table of rows: a name, then the tokens in and out and the cost, all as
 * text. The shared reflow (Table.module.css): cards below 48rem.
 */
function UsageTable({ caption, first, rows }: { caption: string; first: string; rows: Row[] }) {
  return (
    <div className={table.reflowWrap}>
      <table className={`table ${table.reflow}`} role="table">
        <caption className={styles.caption}>{caption}</caption>
        <thead role="rowgroup">
          <tr role="row">
            {[first, ...AMOUNTS].map((name) => (
              <th key={name} scope="col" role="columnheader">
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody role="rowgroup">
          {rows.map((row) => (
            <tr key={row.key} role="row">
              <td role="cell" data-label={first} className={`${styles.strong} ${styles.wrap}`}>
                {row.name}
              </td>
              <td role="cell" data-label="Tokens in" className="tabular">
                {formatNumber(row.totals.input_tokens)}
              </td>
              <td role="cell" data-label="Tokens out" className="tabular">
                {formatNumber(row.totals.output_tokens)}
              </td>
              <td role="cell" data-label="Cost" className="tabular">
                {formatCost(row.totals)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Each day's spend: the number in the cell, and a bar as wide as its share of the costliest day. */
function DailyTable({ report }: { report: UsageReport }) {
  const most = Math.max(0, ...report.by_day.map((day) => day.cost_usd ?? 0));
  return (
    <div className={table.reflowWrap}>
      <table className={`table ${table.reflow}`} role="table">
        <caption className={styles.caption}>Spend by day</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader">
              Day
            </th>
            <th scope="col" role="columnheader">
              Cost
            </th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {report.by_day.map((day) => (
            <tr key={day.day} role="row">
              <td role="cell" data-label="Day">
                {formatDate(`${day.day}T12:00:00+05:30`)}
              </td>
              <td role="cell" data-label="Cost" className={`tabular ${styles.barCell}`}>
                <span>{formatCost(day)}</span>
                <span
                  className={styles.bar}
                  aria-hidden="true"
                  style={{ width: `${most > 0 ? ((day.cost_usd ?? 0) / most) * 100 : 0}%` }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function UsageReportView({
  state,
  params,
  onRetry,
}: {
  state: UsageState;
  params: UsageParams;
  onRetry: () => void;
}) {
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
    return <p className={styles.emptyLine}>AI is off on this server, so nothing is spent.</p>;
  }
  if (state.kind === "empty") return <p className={styles.emptyLine}>No AI use in this period.</p>;

  const { report } = state;
  const warning = budgetWarning(report.budget);
  return (
    <>
      <dl className={styles.tiles}>
        <div className={styles.tile}>
          <dt className={styles.tileLabel}>AI spend</dt>
          <dd className={`${styles.tileValue} tabular`}>{formatCost(report.totals)}</dd>
        </div>
        <div className={styles.tile}>
          <dt className={styles.tileLabel}>Tokens</dt>
          <dd className={`${styles.tileValue} tabular`}>
            {formatNumber(report.totals.input_tokens + report.totals.output_tokens)}
          </dd>
        </div>
        <div className={styles.tile}>
          <dt className={styles.tileLabel}>Budget used this month</dt>
          <dd className={`${styles.tileValue} tabular`}>{budgetShareText(report.budget) ?? "No budget"}</dd>
        </div>
      </dl>
      <p className="tabular">{budgetLine(report.budget)}</p>
      {warning && (
        <p className={styles.budgetWarning} role="status">
          <Icon name="alert" className={styles.errorIcon} />
          {warning}
        </p>
      )}
      <div className={styles.usageTables}>
        <UsageTable
          caption="By user"
          first="User"
          rows={report.by_user.map((row) => ({
            key: row.user_id ?? "none",
            name: row.user_name ?? "No user (work done by the system)",
            totals: row,
          }))}
        />
        <UsageTable
          caption="By model"
          first="Model"
          rows={report.by_model.map((row) => ({ key: row.model, name: row.model_name, totals: row }))}
        />
        <UsageTable
          caption="By kind of work"
          first="Kind"
          rows={report.by_kind.map((row) => ({ key: row.kind, name: usageKindText(row.kind), totals: row }))}
        />
        <UsageTable
          caption="Costliest documents"
          first="Document"
          rows={report.top_documents.map((row, index) => ({
            key: row.document_id ?? `deleted-${index}`,
            name: row.document_id === null ? row.name : <Link href={`/documents/${row.document_id}`}>{row.name}</Link>,
            totals: row,
          }))}
        />
        <DailyTable report={report} />
      </div>
      <p>
        <a className="btn" href={adminUsageCsvUrl(params)} download>
          Download as CSV
        </a>
      </p>
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
  const [result, setResult] = useState<{ key: string; report: UsageReport | null; error: string | null } | null>(
    null,
  );

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
        if (!cancelled) setResult({ key, report, error: null });
      },
      (caught: unknown) => {
        if (!cancelled) {
          const message =
            caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.";
          setResult({ key, report: null, error: message });
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
            User
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
      </div>
      {range ? (
        <UsageReportView
          state={state}
          params={{ ...range, user_id: userId || undefined }}
          onRetry={() => setAttempt((n) => n + 1)}
        />
      ) : (
        <p className="hint">Pick a start date and an end date, the start first.</p>
      )}
    </section>
  );
}
