"use client";

// Quality, a tab of /operator (operator insights, council 11 Oct 2026, F3): how pages
// were read, how questions were answered and how much went to review, for the platform
// and per organisation. Outcome shares below K come from the server as null and say
// "Too few to rate (n)". The answer mix is shown as it is, never as "higher is better":
// "not found" is a correct answer. QualityScreen is rendered in node by
// tests/operatorInsightsViews.test.ts.

import { useRef, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { listOrgs, getOperatorQuality } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { QualityReply, QualityRow } from "@/lib/types";
import { ShareBar } from "./Charts";
import { rateText } from "./insights";
import type { InsightPeriod } from "./insights";
import { LoadFailed } from "./LoadFailed";
import { AI_OFF, PeriodSelect, Skeleton, useLoad } from "./OverviewView";
import type { LoadState } from "./OverviewView";
import { waitWords } from "./platform";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";
import css from "./Insights.module.css";

export const DELETED_NOTE = "Documents deleted for good are not counted.";

export interface QualityData {
  reply: QualityReply;
  names: ReadonlyMap<string, string>; // organisation id to name (GET /operator/orgs)
}

async function loadQuality(days: InsightPeriod): Promise<QualityData> {
  const [reply, orgs] = await Promise.all([getOperatorQuality(days), listOrgs()]);
  return { reply, names: new Map(orgs.items.map((org) => [org.id, org.name])) };
}

export function QualityView() {
  const [days, setDays] = useState<InsightPeriod>(30);
  const [attempt, setAttempt] = useState(0);
  const state = useLoad(days, attempt, loadQuality);
  return <QualityScreen days={days} onDays={setDays} state={state} onRetry={() => setAttempt((n) => n + 1)} />;
}

export function QualityScreen(props: {
  days: InsightPeriod;
  onDays(days: InsightPeriod): void;
  state: LoadState<QualityData>;
  onRetry(): void;
}) {
  const { days, onDays, state, onRetry } = props;
  const heading = useRef<HTMLHeadingElement>(null);
  const data = state.kind === "ready" ? state.data : null;
  const platform = data?.reply.platform ?? null;
  const empty = data !== null && (platform == null || (platform.runs === 0 && platform.answers === 0 && platform.corrections === 0));
  return (
    <section className={admin.section} aria-labelledby="quality-heading">
      <h2 id="quality-heading" ref={heading} tabIndex={-1} className={admin.sectionHeading}>
        Quality
      </h2>
      <p className={styles.summary}>Reading, answers and review. Counts and shares only; no person or document is shown here.</p>
      <div className={styles.toolbar}>
        <PeriodSelect id="quality-period" days={days} onDays={onDays} />
      </div>
      <p className="visually-hidden" role="status">
        {state.kind === "loading" ? "Loading the quality figures\u2026" : state.kind === "ready" && state.refreshing ? "Refreshing\u2026" : ""}
      </p>
      {state.kind === "loading" && <Skeleton />}
      {state.kind === "error" && (
        <LoadFailed
          what="the quality figures"
          message={state.message}
          onRetry={() => {
            heading.current?.focus(); // so focus stays on this screen, not the page, when the banner goes
            onRetry();
          }}
        />
      )}
      {data && !data.reply.ai_enabled && <p className={admin.emptyLine}>{AI_OFF}, so scans are read on this server.</p>}
      {empty && <p className={admin.emptyLine}>Nothing was read, asked or reviewed in this period.</p>}
      {data && platform != null && !empty && (
        <>
          <Reading row={platform} />
          <Answers row={platform} />
          <Review row={platform} />
          <PerOrg data={data} />
        </>
      )}
      {data && <p className={css.footnote}>{DELETED_NOTE}</p>}
    </section>
  );
}

function Facts({ items }: { items: { label: string; value: string; note?: string }[] }) {
  return (
    <dl className={css.facts}>
      {items.map((item) => (
        <div key={item.label} className={css.fact}>
          <dt>{item.label}</dt>
          <dd className="tabular">
            {item.value}
            {item.note && <span className={styles.statusNote}>{item.note}</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

const of = (whole: number, what: string) => `of ${formatNumber(whole)} ${what}`;
const scanned = (row: QualityRow) => row.pages_ai + row.pages_local + row.pages_cached;
const seconds = (value: number | null, whole: number) => (value === null ? rateText(null, whole) : waitWords(value));

function Reading({ row }: { row: QualityRow }) {
  return (
    <>
      <h3 className={admin.tilesHeading}>Reading</h3>
      <ShareBar
        parts={[
          { label: "Spreadsheet cells", value: row.pages_cells },
          { label: "PDF text", value: row.pages_text_layer },
          { label: "Scans", value: row.pages_ocr },
          { label: "Blank pages", value: row.pages_blank },
        ]}
      />
      <Facts
        items={[
          { label: "Unreadable pages", value: rateText(row.pages_unreadable, row.pages_ocr), note: of(row.pages_ocr, "scanned pages") },
          { label: "Read on the backup (this server)", value: rateText(row.pages_local, scanned(row)), note: of(scanned(row), "scanned pages") },
          {
            label: "Failed, caused by the platform",
            value: rateText(row.runs_failed_platform, row.runs),
            note: `${formatNumber(row.runs_failed)} failed in all, ${of(row.runs, "runs")}`,
          },
          { label: "Ready without a document card", value: rateText(row.runs_degraded, row.runs), note: of(row.runs, "runs") },
          {
            label: "Read more than once",
            value: rateText(row.runs - row.documents_run, row.runs),
            note: `${formatNumber(row.documents_run)} documents, ${formatNumber(row.runs)} runs`,
          },
          { label: "Upload to ready, typical", value: seconds(row.ready_p50_seconds, row.ready_documents), note: of(row.ready_documents, "documents") },
          { label: "Upload to ready, slowest 5%", value: seconds(row.ready_p95_seconds, row.ready_documents), note: of(row.ready_documents, "documents") },
        ]}
      />
    </>
  );
}

function Answers({ row }: { row: QualityRow }) {
  const { answered, not_found: notFound, unchecked } = row;
  const perAnswer = row.citations === null || answered === null || answered === 0 ? null : row.citations / answered;
  return (
    <>
      <h3 className={admin.tilesHeading}>Answers</h3>
      {answered === null || notFound === null || unchecked === null ? (
        <p className={admin.emptyLine}>Answer mix: {rateText(null, row.answers)}</p>
      ) : (
        <ShareBar
          parts={[
            { label: "Answered", value: answered },
            { label: "Not found", value: notFound },
            { label: "Needs a check", value: unchecked },
          ]}
        />
      )}
      <p className={css.footnote}>&quot;Not found&quot; is correct when the documents do not hold the answer, so more answered is not always better.</p>
      <Facts
        items={[
          { label: "Answers given", value: formatNumber(row.answers) },
          {
            label: "Verified, of those answered",
            value: answered === null ? rateText(null, row.answers) : rateText(row.answers_verified, answered),
          },
          { label: "From a fixed query", value: rateText(row.answers_fixed_query, row.answers), note: of(row.answers, "answers") },
          {
            label: "Sources per answer",
            value: perAnswer === null ? rateText(null, row.answers) : formatNumber(Math.round(perAnswer * 10) / 10),
          },
          { label: "Thumbs up", value: rateText(row.feedback_up, row.feedback), note: of(row.feedback, "ratings") },
        ]}
      />
    </>
  );
}

function Review({ row }: { row: QualityRow }) {
  const { corrections, corrections_second: second, corrections_typed: typed } = row;
  const first = second === null || typed === null ? null : corrections - second - typed;
  return (
    <>
      <h3 className={admin.tilesHeading}>Review</h3>
      <Facts
        items={[
          { label: "Needs review when read", value: rateText(row.values_needs_review, row.values_total), note: of(row.values_total, "values") },
          { label: "Fixes made in review", value: formatNumber(corrections) },
          { label: "Fixed with the first suggestion", value: rateText(first, corrections), note: of(corrections, "fixes") },
          { label: "Fixed with the second suggestion", value: rateText(second, corrections), note: of(corrections, "fixes") },
          { label: "Typed by hand", value: rateText(typed, corrections), note: of(corrections, "fixes") },
        ]}
      />
    </>
  );
}

const ORG_COLUMNS = ["Organisation", "Runs", "Failed, platform", "Ready without a card", "On the backup", "Answers", "Not found", "Needs review"];

function PerOrg({ data }: { data: QualityData }) {
  const rows = data.reply.organisations
    .map((row) => ({ row, name: (row.organisation_id && data.names.get(row.organisation_id)) || "Unknown organisation" }))
    .sort((a, b) => a.name.localeCompare(b.name));
  if (rows.length === 0) return null;
  return (
    <div className={table.reflowWrap}>
      <table className={`table ${table.reflow}`} role="table">
        <caption className={admin.caption}>Per organisation</caption>
        <thead role="rowgroup">
          <tr role="row">
            {ORG_COLUMNS.map((label) => (
              <th key={label} scope="col" role="columnheader">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody role="rowgroup">
          {rows.map(({ row, name }) => {
            const cells = [
              formatNumber(row.runs),
              rateText(row.runs_failed_platform, row.runs),
              rateText(row.runs_degraded, row.runs),
              rateText(row.pages_local, scanned(row)),
              formatNumber(row.answers),
              rateText(row.not_found, row.answers),
              rateText(row.values_needs_review, row.values_total),
            ];
            return (
              <tr key={row.organisation_id ?? name} role="row">
                <td role="cell" className={styles.orgName}>
                  {name}
                </td>
                {cells.map((cell, index) => (
                  <td key={ORG_COLUMNS[index + 1]} role="cell" data-label={ORG_COLUMNS[index + 1]} className="tabular">
                    {cell}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
