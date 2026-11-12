"use client";

// Each member's numbers (#386), under the organisation's numbers on the Numbers tab: what each
// person of this organisation did in the period (this month, the last 30 days or all time):
// files uploaded, pages of them read, questions asked, values verified, and when they were last
// active. The server sends this organisation's people only, and never money or tokens. Sorted
// by any column (a header button, aria-sort); a card per person on a phone (the shared reflow).
// MembersTableView is the view, rendered in node by tests/admin-members.test.ts.

import { useEffect, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { roleName } from "@/components/shell/nav";
import { Icon } from "@/components/ui/Icon";
import { getMemberNumbers } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import { formatDateTime, formatNumber } from "@/lib/format";
import type { MemberPeriod, MembersReply } from "@/lib/types";
import { MEMBER_COLUMNS, MEMBER_PERIODS, nextSort, sortMembers } from "./logic";
import type { MemberColumn, SortState } from "./logic";
import { SortHeader } from "./SortHeader";
import styles from "./Admin.module.css";

export type MembersState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; reply: MembersReply };

export const MEMBERS_NOTE =
  "Files uploaded counts every version, those in Trash too. Pages read are the pages of those files that were read, each page once. A question the assistant answered with a question back is not counted.";

export function MembersTable() {
  const [period, setPeriod] = useState<MemberPeriod>("month");
  const [attempt, setAttempt] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; state: MembersState } | null>(null);
  const key = `${period}|${attempt}`;

  useEffect(() => {
    let cancelled = false;
    const done = (state: MembersState) => !cancelled && setLoaded({ key, state });
    getMemberNumbers(period).then(
      (reply) => done({ kind: "ready", reply }),
      (caught: unknown) =>
        done({
          kind: "error",
          message: errorWords(caught),
        }),
    );
    return () => {
      cancelled = true;
    };
  }, [key, period]);

  const state: MembersState = loaded?.key === key ? loaded.state : { kind: "loading" };
  return (
    <MembersTableView period={period} onPeriod={setPeriod} state={state} onRetry={() => setAttempt((n) => n + 1)} />
  );
}

export function MembersTableView({
  period,
  onPeriod,
  state,
  onRetry,
}: {
  period: MemberPeriod;
  onPeriod(period: MemberPeriod): void;
  state: MembersState;
  onRetry(): void;
}) {
  const [sort, setSort] = useState<SortState<MemberColumn>>({ column: "name", dir: "ascending" });
  const periodLabel = MEMBER_PERIODS.find((option) => option.id === period)?.label ?? "";
  return (
    <section className={styles.section} aria-labelledby="members-heading">
      <h3 id="members-heading" className={styles.tilesHeading}>
        Each member
      </h3>
      <div className={styles.usageControls}>
        <div className="field">
          <label className="label" htmlFor="members-period">
            Period
          </label>
          <select
            id="members-period"
            className="input"
            value={period}
            onChange={(event) => {
              const chosen = MEMBER_PERIODS.find((option) => option.id === event.target.value);
              if (chosen) onPeriod(chosen.id);
            }}
          >
            {MEMBER_PERIODS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>
      {state.kind === "loading" && (
        <p className="muted" role="status">
          Loading each member&apos;s numbers…
        </p>
      )}
      {state.kind === "error" && (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>Could not load each member&apos;s numbers.</p>
            <p>{state.message}</p>
          </div>
          <button type="button" className="btn" onClick={onRetry}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {state.kind === "ready" && (
        <>
          <div className={`${table.reflowWrap} ${styles.tableScroll}`}>
            <table className={`table ${table.reflow}`} role="table">
              <caption className="visually-hidden">
                Each member&apos;s numbers, {periodLabel.toLocaleLowerCase()},{" "}
                {MEMBER_COLUMNS.find((column) => column.id === sort.column)?.label.toLocaleLowerCase()} {sort.dir}
              </caption>
              <thead role="rowgroup">
                <tr role="row">
                  {MEMBER_COLUMNS.map((column) => (
                    <SortHeader
                      key={column.id}
                      column={column.id}
                      label={column.label}
                      sort={sort}
                      onSort={(id) => setSort(nextSort(sort, id, "name"))}
                      num={column.id !== "name" && column.id !== "last_active_at"}
                    />
                  ))}
                </tr>
              </thead>
              <tbody role="rowgroup">
                {sortMembers(state.reply.members, sort).map((member) => (
                  <tr key={member.user_id} role="row">
                    <td role="cell">
                      {member.display_name}
                      <span className={styles.cellNote}>
                        {roleName(member.role)}
                        {member.disabled && ", turned off"} · {member.email}
                      </span>
                    </td>
                    <td role="cell" data-label="Files uploaded" className="num">
                      {formatNumber(member.documents_uploaded)}
                    </td>
                    <td role="cell" data-label="Pages read" className="num">
                      {formatNumber(member.pages_read)}
                    </td>
                    <td role="cell" data-label="Questions asked" className="num">
                      {formatNumber(member.questions_asked)}
                    </td>
                    <td role="cell" data-label="Values verified" className="num">
                      {formatNumber(member.values_verified)}
                    </td>
                    <td role="cell" data-label="Last active">
                      {member.last_active_at === null ? "Never" : formatDateTime(member.last_active_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={styles.tileNote}>{MEMBERS_NOTE}</p>
        </>
      )}
    </section>
  );
}
