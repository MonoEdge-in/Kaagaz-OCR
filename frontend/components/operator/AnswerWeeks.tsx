"use client";

// Answers by week, under Quality on /operator (PRD 6 F3, GET /operator/answer-weeks): per
// organisation and ended week, replies, answered, not found, clarify, rated and Useful. Counts
// only. A count the server hid (null: under 10, or what would give one away) says "Hidden
// (under 10)"; it is never worked out here. AnswerWeeksScreen is rendered in node by
// tests/operatorInsightsViews.test.ts.

import { useRef, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { getAnswerWeeks, listOrgs } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import type { AnswerWeeksReply } from "@/lib/types";
import { LoadFailed } from "./LoadFailed";
import { Freshness, Skeleton, useLoad } from "./OverviewView";
import type { LoadState } from "./OverviewView";
import { MoreRows, useRowCap } from "@/components/admin/RowCap";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";
import css from "./Insights.module.css";

export const HIDDEN = "Hidden (under 10)";
const PERIODS = [4, 12] as const;
export type AnswerWeeksPeriod = (typeof PERIODS)[number];

export interface AnswerWeeksData {
  reply: AnswerWeeksReply;
  names: ReadonlyMap<string, string>; // organisation id to name (GET /operator/orgs)
  loadedAt?: number;
}

async function loadWeeks(weeks: AnswerWeeksPeriod): Promise<AnswerWeeksData> {
  const [reply, orgs] = await Promise.all([getAnswerWeeks(weeks), listOrgs()]);
  return { reply, names: new Map(orgs.items.map((org) => [org.id, org.name])), loadedAt: Date.now() };
}

export function AnswerWeeksView() {
  const [weeks, setWeeks] = useState<AnswerWeeksPeriod>(4);
  const [attempt, setAttempt] = useState(0);
  const state = useLoad(weeks, attempt, loadWeeks);
  return <AnswerWeeksScreen weeks={weeks} onWeeks={setWeeks} state={state} onRetry={() => setAttempt((n) => n + 1)} />;
}

export function AnswerWeeksScreen(props: {
  weeks: AnswerWeeksPeriod;
  onWeeks(weeks: AnswerWeeksPeriod): void;
  state: LoadState<AnswerWeeksData>;
  onRetry(): void;
}) {
  const { weeks, onWeeks, state, onRetry } = props;
  const heading = useRef<HTMLHeadingElement>(null);
  const data = state.kind === "ready" ? state.data : null;
  // No status line of its own: the Quality tab keeps one, QualityView's; the skeleton shows loading.
  return (
    <section className={admin.section} aria-labelledby="answer-weeks-heading">
      <h2 id="answer-weeks-heading" ref={heading} tabIndex={-1} className={admin.sectionHeading}>
        Answers by week
      </h2>
      <p className={styles.summary}>Replies per organisation and week, Monday to Sunday, for weeks that have ended. Counts only.</p>
      <div className={styles.toolbar}>
        <div className="field">
          <label htmlFor="answer-weeks-period">Period</label>
          <select
            id="answer-weeks-period"
            className="input"
            value={weeks}
            onChange={(event) => {
              const chosen = PERIODS.find((option) => String(option) === event.target.value);
              if (chosen) onWeeks(chosen);
            }}
          >
            {PERIODS.map((option) => (
              <option key={option} value={option}>
                Last {option} weeks
              </option>
            ))}
          </select>
        </div>
        {data?.loadedAt !== undefined && (
          <div className={css.toolEnd}>
            <Freshness loadedAt={data.loadedAt} refreshing={state.kind === "ready" && state.refreshing === true} onRefresh={onRetry} />
          </div>
        )}
      </div>
      {state.kind === "loading" && <Skeleton />}
      {state.kind === "error" && (
        <LoadFailed
          what="the answers by week"
          message={state.message}
          onRetry={() => {
            heading.current?.focus(); // so focus stays on this screen, not the page, when the banner goes
            onRetry();
          }}
        />
      )}
      {data && data.reply.organisations.length === 0 && <p className={admin.emptyLine}>No replies in these weeks.</p>}
      {data && data.reply.organisations.length > 0 && <WeeksTable data={data} />}
      {data && (
        <p className={css.footnote}>
          {HIDDEN}: fewer than 10, or a count that would give one away. A rating on a clarifying question is not counted.
        </p>
      )}
    </section>
  );
}

const COLUMNS = ["Organisation", "Week of", "Replies", "Answered", "Not found", "Clarify", "Rated", "Useful"];
const count = (value: number | null) => (value === null ? HIDDEN : formatNumber(value));

function WeeksTable({ data }: { data: AnswerWeeksData }) {
  const cap = useRowCap();
  const rows = data.reply.organisations
    .map((row) => ({ row, name: data.names.get(row.organisation_id) ?? "Unknown organisation" }))
    .sort((a, b) => a.name.localeCompare(b.name) || a.row.week.localeCompare(b.row.week));
  return (
    <>
      <div className={`${table.reflowWrap} ${admin.tableScroll}`}>
        <table className={`table ${table.reflow} ${styles.nameWide} ${styles.compare}`} role="table">
          <caption className={admin.caption}>Answers per organisation and week</caption>
          <thead role="rowgroup">
            <tr role="row">
              {COLUMNS.map((label, index) => (
                <th key={label} scope="col" role="columnheader" className={index > 1 ? "num" : undefined}>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody role="rowgroup">
            {rows.slice(0, cap.limit).map(({ row, name }) => {
              const cells = [
                formatDate(row.week),
                formatNumber(row.replies),
                count(row.answered),
                count(row.not_found),
                count(row.clarify),
                formatNumber(row.rated),
                count(row.useful),
              ];
              return (
                <tr key={`${row.organisation_id}:${row.week}`} role="row">
                  <td role="cell" className={styles.orgName}>
                    {name}
                  </td>
                  {cells.map((cell, index) => (
                    <td key={COLUMNS[index + 1]} role="cell" data-label={COLUMNS[index + 1]} className={index > 0 ? "num" : undefined}>
                      {cell}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <MoreRows shown={Math.min(cap.limit, rows.length)} total={rows.length} what="rows" onMore={cap.showMore} />
    </>
  );
}
