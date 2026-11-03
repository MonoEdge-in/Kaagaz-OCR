"use client";

// Each organisation's numbers (#387), under the Overview: for the period, the files each
// organisation uploaded, every page of them read by how it was read (an AI model, the local
// reader, the PDF's own text, spreadsheet cells, blank; a route added later shows under its
// own name, #377), the files that failed by reason, active people, questions and AI use.
// Counts, codes and organisation names only (0055's kaagaz_op_org_numbers). Refused requests
// are not here: the server writes them to its log only. Sorted by any column; a card per
// organisation on a phone. OrgNumbersScreen is rendered in node by tests/operator-org-numbers.test.ts.

import { useRef, useState } from "react";
import { nextSort, sortedBy } from "@/components/admin/logic";
import type { SortState } from "@/components/admin/logic";
import { SortHeader } from "@/components/admin/SortHeader";
import table from "@/components/documents/Table.module.css";
import { getOrgNumbers, listOrgs } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { OrgNumbers, OrgNumbersReply } from "@/lib/types";
import type { InsightPeriod } from "./insights";
import { LoadFailed } from "./LoadFailed";
import { countText } from "./logic";
import { usd } from "./models";
import { AI_OFF, Freshness, PeriodSelect, Skeleton, useLoad } from "./OverviewView";
import type { LoadState } from "./OverviewView";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";
import css from "./Insights.module.css";

/** How a page was read, in words; any other route is shown by its own name. */
export const ROUTE_WORDS: Readonly<Record<string, string>> = {
  ai: "AI model",
  local: "Local reader",
  text_layer: "PDF's own text",
  cells: "Spreadsheet cells",
  blank: "Blank",
  ocr: "Scanned, reader not recorded",
};
const ROUTE_ORDER = Object.keys(ROUTE_WORDS);

export const ORG_NUMBERS_NOTE =
  "Files uploaded in the period, every version, those in Trash too; files deleted for good are not counted. Pages read are the pages of those files that were read, each page once, by how they were read. Active people did anything the activity log records. Questions do not count a question asked back. AI cost is what OpenRouter reported for Gemini, and Mistral's price a page for Mistral. Refused requests are not counted here: the server writes them to its log only.";

export interface OrgNumbersData {
  reply: OrgNumbersReply;
  names: ReadonlyMap<string, string>; // organisation id to name; empty without the list
  loadedAt: number;
}

async function loadOrgNumbers(days: InsightPeriod): Promise<OrgNumbersData> {
  // The names only label the rows: without them each row says "Unknown organisation".
  const [reply, orgs] = await Promise.all([getOrgNumbers(days), listOrgs().catch(() => null)]);
  return { reply, names: new Map((orgs?.items ?? []).map((org) => [org.id, org.name])), loadedAt: Date.now() };
}

export function OrgNumbersView() {
  const [days, setDays] = useState<InsightPeriod>(30);
  const [attempt, setAttempt] = useState(0);
  const state = useLoad(days, attempt, loadOrgNumbers);
  return <OrgNumbersScreen days={days} onDays={setDays} state={state} onRetry={() => setAttempt((n) => n + 1)} />;
}

/** {key: count} as "AI model 4, Local reader 2": known routes first, in their order, then the rest by name. */
export function breakdownLines(counts: Readonly<Record<string, number>>, words: Readonly<Record<string, string>> = {}): string[] {
  const order = (key: string) => (ROUTE_ORDER.includes(key) ? ROUTE_ORDER.indexOf(key) : ROUTE_ORDER.length);
  return Object.keys(counts)
    .filter((key) => (counts[key] ?? 0) > 0)
    .sort((a, b) => order(a) - order(b) || a.localeCompare(b))
    .map((key) => `${words[key] ?? key.replace(/_/g, " ")} ${formatNumber(counts[key] ?? 0)}`);
}

const total = (counts: Readonly<Record<string, number>>) => Object.values(counts).reduce((sum, n) => sum + n, 0);

type Column = "name" | "documents" | "pages_read" | "failed" | "active_users" | "questions" | "ai_calls";
const COLUMNS: { id: Column; label: string }[] = [
  { id: "name", label: "Organisation" },
  { id: "documents", label: "Files uploaded" },
  { id: "pages_read", label: "Pages read" },
  { id: "failed", label: "Failed files" },
  { id: "active_users", label: "Active people" },
  { id: "questions", label: "Questions" },
  { id: "ai_calls", label: "AI use" },
];

export function OrgNumbersScreen(props: {
  days: InsightPeriod;
  onDays(days: InsightPeriod): void;
  state: LoadState<OrgNumbersData>;
  onRetry(): void;
}) {
  const { days, onDays, state, onRetry } = props;
  const heading = useRef<HTMLHeadingElement>(null);
  const data = state.kind === "ready" ? state.data : null;
  return (
    <section className={admin.section} aria-labelledby="org-numbers-heading">
      <h2 id="org-numbers-heading" ref={heading} tabIndex={-1} className={admin.sectionHeading}>
        Each organisation&apos;s numbers
      </h2>
      <div className={styles.toolbar}>
        <PeriodSelect id="org-numbers-period" days={days} onDays={onDays} />
        {data && (
          <div className={css.toolEnd}>
            <Freshness loadedAt={data.loadedAt} refreshing={state.kind === "ready" && state.refreshing === true} onRefresh={onRetry} />
          </div>
        )}
      </div>
      {state.kind === "loading" && <Skeleton />}
      {state.kind === "error" && (
        <LoadFailed
          what="each organisation's numbers"
          message={state.message}
          onRetry={() => {
            heading.current?.focus(); // so focus stays on this screen, not the page, when the banner goes
            onRetry();
          }}
        />
      )}
      {data && data.reply.organisations.length === 0 && <p className={admin.emptyLine}>No organisations yet.</p>}
      {data && data.reply.organisations.length > 0 && <NumbersTable data={data} />}
      {data && <p className={css.footnote}>{ORG_NUMBERS_NOTE}</p>}
    </section>
  );
}

const UNKNOWN_ORG = "Unknown organisation";

function NumbersTable({ data }: { data: OrgNumbersData }) {
  const [sort, setSort] = useState<SortState<Column>>({ column: "name", dir: "ascending" });
  const { reply, names } = data;
  const name = (row: OrgNumbers) => names.get(row.organisation_id) ?? UNKNOWN_ORG;
  const value = (row: OrgNumbers): number | string => {
    if (sort.column === "name") return name(row);
    if (sort.column === "failed") return total(row.failed_documents);
    return row[sort.column];
  };
  const rows = sortedBy(reply.organisations, value, sort.dir, name);
  const all = (pick: (row: OrgNumbers) => number) => reply.organisations.reduce((sum, row) => sum + pick(row), 0);
  return (
    <>
      <p className={styles.summary}>
        In all: {countText(all((row) => row.documents), "file", "files")} uploaded,{" "}
        {countText(all((row) => row.pages_read), "page", "pages")} read,{" "}
        {countText(all((row) => row.questions), "question", "questions")}.
      </p>
      <div className={`${table.reflowWrap} ${admin.tableScroll}`}>
        <table className={`table ${table.reflow}`} role="table">
          <caption className="visually-hidden">
            Each organisation&apos;s numbers, {reply.from} to {reply.to},{" "}
            {COLUMNS.find((column) => column.id === sort.column)?.label.toLocaleLowerCase()} {sort.dir}
          </caption>
          <thead role="rowgroup">
            <tr role="row">
              {COLUMNS.map((column) => (
                <SortHeader
                  key={column.id}
                  column={column.id}
                  label={column.label}
                  sort={sort}
                  onSort={(id) => setSort(nextSort(sort, id, "name"))}
                  num={column.id !== "name"}
                />
              ))}
            </tr>
          </thead>
          <tbody role="rowgroup">
            {rows.map((row) => (
              <NumbersRow key={row.organisation_id} row={row} name={name(row)} aiOn={reply.ai_enabled} />
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Breakdown({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <ul className={admin.cellList}>
      {lines.map((line) => (
        <li key={line}>{line}</li>
      ))}
    </ul>
  );
}

function NumbersRow({ row, name, aiOn }: { row: OrgNumbers; name: string; aiOn: boolean }) {
  const failed = total(row.failed_documents);
  return (
    <tr role="row">
      <td role="cell" className={styles.orgName}>
        {name}
      </td>
      <td role="cell" data-label="Files uploaded" className="num">
        {formatNumber(row.documents)}
        {row.documents_in_trash > 0 && <span className={admin.cellNote}>{formatNumber(row.documents_in_trash)} in Trash</span>}
      </td>
      <td role="cell" data-label="Pages read" className="num">
        {formatNumber(row.pages_read)}
        <Breakdown lines={breakdownLines(row.pages_by_route, ROUTE_WORDS)} />
      </td>
      <td role="cell" data-label="Failed files" className="num">
        {formatNumber(failed)}
        <Breakdown lines={breakdownLines(row.failed_documents)} />
      </td>
      <td role="cell" data-label="Active people" className="num">
        {formatNumber(row.active_users)}
      </td>
      <td role="cell" data-label="Questions" className="num">
        {formatNumber(row.questions)}
      </td>
      <td role="cell" data-label="AI use" className="num">
        {!aiOn && row.ai_calls === 0 ? (
          AI_OFF
        ) : (
          <>
            {countText(row.ai_calls, "call", "calls")}
            <span className={admin.cellNote}>
              {formatNumber(row.ai_input_tokens)} tokens in, {formatNumber(row.ai_output_tokens)} out
            </span>
            <span className={admin.cellNote}>
              {usd(row.ai_cost_usd)}
              {row.ai_cost_unknown_calls > 0 && `, ${countText(row.ai_cost_unknown_calls, "call", "calls")} cost not known`}
            </span>
          </>
        )}
      </td>
    </tr>
  );
}
