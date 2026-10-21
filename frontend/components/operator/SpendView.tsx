"use client";

// AI spend, a tab of /operator (model choice, council 11 Oct 2026, SP3): tokens and US
// dollars per organisation and model over a chosen period, with the totals. Never a
// person or a document: the server sends neither. A cost that is partly the catalogue
// price times the tokens says "(estimated)" in words.

import { useEffect, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { getOperatorUsage } from "@/lib/api";
import { formatCost, formatNumber } from "@/lib/format";
import type { OperatorUsageRow, UsageTotals } from "@/lib/types";
import { LoadFailed } from "./LoadFailed";
import { operatorErrorMessage } from "./logic";
import { PERIODS, periodRange, spendTotals } from "./models";
import { Freshness } from "./OverviewView";
import type { PeriodId } from "./models";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";
import css from "./Insights.module.css";

export function SpendView() {
  const [period, setPeriod] = useState<PeriodId>("month");
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; rows: OperatorUsageRow[]; at: number } | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const key = `${period}|${attempt}`;

  useEffect(() => {
    let cancelled = false;
    getOperatorUsage(periodRange(period, new Date())).then(
      (result) => !cancelled && setLoaded({ key, rows: result.items, at: Date.now() }),
      (caught: unknown) => !cancelled && setError({ key, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [period, attempt, key]);

  const rows = loaded?.key === key ? loaded.rows : null;
  const loadedAt = loaded?.key === key ? loaded.at : null;
  const failed = error?.key === key ? error.message : null;
  return (
    <section className={admin.section} aria-labelledby="spend-heading">
      <h2 id="spend-heading" className={admin.sectionHeading}>
        AI spend
      </h2>
      <p className={styles.summary}>Per organisation and model. No person or document is shown here.</p>
      <div className={styles.toolbar}>
        <div className="field">
          <label htmlFor="spend-period">Period</label>
          <select
            id="spend-period"
            className="input"
            value={period}
            onChange={(event) => {
              const chosen = PERIODS.find((option) => option.id === event.target.value);
              if (chosen) setPeriod(chosen.id);
            }}
          >
            {PERIODS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        {loadedAt !== null && (
          <div className={css.toolEnd}>
            <Freshness loadedAt={loadedAt} refreshing={false} onRefresh={() => setAttempt((n) => n + 1)} />
          </div>
        )}
      </div>
      <p className="visually-hidden" role="status">
        {!rows && !failed ? "Loading the spend…" : ""}
      </p>
      {failed && <LoadFailed what="the spend" message={failed} onRetry={() => setAttempt((n) => n + 1)} />}
      {rows && rows.length === 0 && <p className={admin.emptyLine}>No AI use in this period.</p>}
      {rows && rows.length > 0 && <SpendTable rows={rows} />}
    </section>
  );
}

function Cells({ totals }: { totals: UsageTotals }) {
  return (
    <>
      <td role="cell" data-label="Calls" className="num">
        {formatNumber(totals.calls)}
        {totals.cached_calls > 0 && (
          <span className={styles.statusNote}>{formatNumber(totals.cached_calls)} from the page cache</span>
        )}
      </td>
      <td role="cell" data-label="Tokens in" className="num">
        {formatNumber(totals.input_tokens)}
      </td>
      <td role="cell" data-label="Tokens out" className="num">
        {formatNumber(totals.output_tokens)}
      </td>
      <td role="cell" data-label="Cost" className="num">
        {formatCost(totals)}
      </td>
    </>
  );
}

export function SpendTable({ rows }: { rows: OperatorUsageRow[] }) {
  const totals = spendTotals(rows);
  return (
    <div className={`${table.reflowWrap} ${admin.tableScroll}`}>
      <table className={`table ${table.reflow}`} role="table">
        <caption className="visually-hidden">AI spend per organisation and model</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader">Organisation</th>
            <th scope="col" role="columnheader">Model</th>
            <th scope="col" role="columnheader" className="num">Calls</th>
            <th scope="col" role="columnheader" className="num">Tokens in</th>
            <th scope="col" role="columnheader" className="num">Tokens out</th>
            <th scope="col" role="columnheader" className="num">Cost</th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {rows.map((row) => (
            <tr key={`${row.organisation_id}|${row.model}`} role="row">
              <td role="cell" className={styles.orgName}>
                {row.organisation_name}
              </td>
              <td role="cell" data-label="Model">
                {row.model}
              </td>
              <Cells totals={row} />
            </tr>
          ))}
        </tbody>
        <tfoot role="rowgroup">
          <tr role="row">
            <th scope="row" role="rowheader" colSpan={2}>
              Total
            </th>
            <Cells totals={totals} />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
