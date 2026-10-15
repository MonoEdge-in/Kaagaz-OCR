"use client";

// The organisation's AI use (model choice SP2, SP4): pick a period (Today,
// Last 7 days, This month, or two dates) and, if wanted, one user; then the
// tokens used, the tokens by day (bars drawn with CSS, the number in the cell; no
// chart library), by user, kind of work and the top documents. Org admins and their
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
import { formatDate, formatNumber } from "@/lib/format";
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
function UsageTable({ caption, first, rows }: { caption: string; first: string; rows: Row[] }) {
  const sorted = [...rows].sort((a, b) => usageTokens(b.totals) - usageTokens(a.totals));
  return (
    <div className={table.reflowWrap}>
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
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Each day's tokens: the number in the cell, and a bar as wide as its share of the heaviest day. */
function DailyTable({ report }: { report: UsageReport }) {
  const most = Math.max(0, ...report.by_day.map(usageTokens));
  return (
    <div className={table.reflowWrap}>
      <table className={`table ${table.reflow}`} role="table">
        <caption className={styles.caption}>Tokens by day</caption>
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
          {report.by_day.map((day) => (
            <tr key={day.day} role="row">
              <td role="cell" data-label="Day">
                {formatDate(`${day.day}T12:00:00+05:30`)}
              </td>
              <td role="cell" data-label="Tokens" className={`num ${styles.barCell}`}>
                <span>{formatNumber(usageTokens(day))}</span>
                <span
                  className={styles.bar}
                  aria-hidden="true"
                  style={{ width: `${most > 0 ? (usageTokens(day) / most) * 100 : 0}%` }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
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
  return (
    <>
      <dl className={styles.tiles}>
        <div className={styles.tile}>
          <dt className={styles.tileLabel}>Tokens in this period</dt>
          <dd className={`${styles.tileValue} tabular`}>{formatNumber(usageTokens(report.totals))}</dd>
        </div>
      </dl>
      <div className={styles.usageTables}>
        <DailyTable report={report} />
        <UsageTable
          caption="By user"
          first="User"
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
        <UsageReportView state={state} onRetry={() => setAttempt((n) => n + 1)} />
      ) : (
        <p className="hint">Pick a start date and an end date, the start first.</p>
      )}
    </section>
  );
}
