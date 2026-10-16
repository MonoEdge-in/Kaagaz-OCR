"use client";

// Compare models, under the catalogue on the Models tab (operator insights, council 11
// Oct 2026, F4): what each model read, cost, how fast, and the page signals the checker
// and the reader leave (to backup, refused, checker disagreement, unpinned). Observational,
// not an A/B test: each model read other documents for other organisations. A model with
// fewer than 30 documents says "too few" rather than a share. Never the reader's own
// unsure words. ModelBoardScreen is rendered in node by tests/operatorInsightsViews.test.ts.

import { useRef, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { getAiDefaults, getModelBoard, getOperatorModels } from "@/lib/api";
import { formatCost, formatNumber } from "@/lib/format";
import type { BoardReply } from "@/lib/types";
import { BOARD_MIN_DOCUMENTS, boardBest, boardRows, rateText } from "./insights";
import type { BoardBest, BoardLine, InsightPeriod } from "./insights";
import { LoadFailed } from "./LoadFailed";
import { jobText, usd } from "./models";
import { AI_OFF, PeriodSelect, Skeleton, useLoad } from "./OverviewView";
import type { LoadState } from "./OverviewView";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";
import css from "./Insights.module.css";

export const OBSERVATIONAL =
  "Observational, not an A/B test: each model read other documents for other organisations, so a difference may come from the documents, not the model.";
export const TOO_FEW = "Too few to compare";
const LOCAL_READER = "rapidocr";

export interface BoardData {
  reply: BoardReply;
  names: ReadonlyMap<string, string>; // model id to name, from the catalogue
  jobs?: ReadonlyMap<string, string>; // model id to its job in words (board 8); none without the catalogue
}

async function loadBoard(days: InsightPeriod): Promise<BoardData> {
  // The catalogue and the defaults only give the names and jobs: without them the board shows the model ids.
  const [reply, catalogue, platform] = await Promise.all([
    getModelBoard(days),
    getOperatorModels().catch(() => null),
    getAiDefaults().catch(() => null),
  ]);
  const items = catalogue?.items ?? [];
  return {
    reply,
    names: new Map(items.map((model) => [model.id, model.name])),
    jobs: new Map(items.map((model) => [model.id, jobText(model, platform)])),
  };
}

export function ModelBoard() {
  const [days, setDays] = useState<InsightPeriod>(30);
  const [attempt, setAttempt] = useState(0);
  const state = useLoad(days, attempt, loadBoard);
  return <ModelBoardScreen days={days} onDays={setDays} state={state} onRetry={() => setAttempt((n) => n + 1)} />;
}

export function ModelBoardScreen(props: {
  days: InsightPeriod;
  onDays(days: InsightPeriod): void;
  state: LoadState<BoardData>;
  onRetry(): void;
}) {
  const { days, onDays, state, onRetry } = props;
  const heading = useRef<HTMLHeadingElement>(null);
  const data = state.kind === "ready" ? state.data : null;
  // No status line of its own: the Models tab keeps one, ModelsView's; the skeleton shows loading.
  return (
    <section className={admin.section} aria-labelledby="board-heading">
      <h2 id="board-heading" ref={heading} tabIndex={-1} className={admin.sectionHeading}>
        Compare models
      </h2>
      <p className={styles.summary}>{OBSERVATIONAL}</p>
      <div className={styles.toolbar}>
        <PeriodSelect id="board-period" days={days} onDays={onDays} />
      </div>
      {state.kind === "loading" && <Skeleton />}
      {state.kind === "error" && (
        <LoadFailed
          what="the model comparison"
          message={state.message}
          onRetry={() => {
            heading.current?.focus(); // so focus stays on this screen, not the page, when the banner goes
            onRetry();
          }}
        />
      )}
      {data && !data.reply.ai_enabled && <p className={admin.emptyLine}>{AI_OFF}, so only this server&apos;s reader is shown.</p>}
      {data && data.reply.items.length === 0 && <p className={admin.emptyLine}>No model read or answered anything in this period.</p>}
      {data && data.reply.items.length > 0 && <BoardTable data={data} />}
      {data && (
        <p className={css.footnote}>
          A model with fewer than {BOARD_MIN_DOCUMENTS} documents is too few to compare. Documents deleted for good are not
          counted; their AI cost is.
        </p>
      )}
    </section>
  );
}

// Board 8 P2: the figure columns line up at the end (.num).
const COLUMNS = [
  { label: "Model", num: false },
  { label: "Pages", num: true },
  { label: "$ per 1,000 pages (est.)", num: true },
  { label: "Cache saved", num: true },
  { label: "Seconds per page", num: true },
  { label: "Page signals", num: false },
  { label: "Failed or without a card", num: true },
  { label: "AI cost", num: true },
];

function BoardTable({ data }: { data: BoardData }) {
  const lines = boardRows(data.reply.items);
  const best = boardBest(lines);
  return (
    <div className={table.reflowWrap}>
      <table className={`table ${table.reflow} ${styles.nameWide}`} role="table">
        <caption className={admin.caption}>Compare models, observational</caption>
        <thead role="rowgroup">
          <tr role="row">
            {COLUMNS.map(({ label, num }) => (
              <th key={label} scope="col" role="columnheader" className={num ? "num" : undefined}>
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody role="rowgroup">
          {lines.map((line, i) => (
            <BoardLineRow
              key={line.row.model}
              line={line}
              best={best[i]}
              name={modelName(line.row.model, data.names)}
              job={data.jobs?.get(line.row.model)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

const modelName = (id: string, names: ReadonlyMap<string, string>) =>
  id === LOCAL_READER ? "RapidOCR (this server)" : (names.get(id) ?? id);

const DASH = "\u2014";
const cacheText = (value: number | null) => (value === null ? DASH : `${Math.round(value * 100)}% of scan calls`);
// The server nulls a speed on too few timed runs and a page signal on too few scanned
// documents: neither basis is a count the board has, so no n is shown.
const secondsText = (label: string, value: number | null) =>
  value === null ? `${label}: too few timed runs` : `${label} ${formatNumber(Math.round(value * 10) / 10)} s`;
const signalText = (value: number | null, ocrPages: number) => (value === null ? "Too few scanned documents to rate" : rateText(value, ocrPages));

// Board 12 P8: the column's best value says so in a word, not by colour alone; the space keeps it a word for screen readers.
const Best = ({ word, on }: { word: string; on: boolean | undefined }) =>
  on ? (
    <>
      {" "}
      <span className={css.best}>{word}</span>
    </>
  ) : null;

function BoardLineRow(props: { line: BoardLine; best: BoardBest | undefined; name: string; job: string | undefined }) {
  const { line, best, name, job } = props;
  const { row } = line;
  const reads = row.runs > 0 || line.pages > 0; // chat and embedding models only spend
  const compare = (text: string) => (!reads ? <span className="muted">Does not read pages</span> : line.tooFew ? TOO_FEW : text);
  const signals = [
    ["To backup", row.pages_fell_back],
    ["Refused", row.pages_refused],
    ["Checker disagreement", row.pages_disagree],
    ["Unpinned", row.pages_unpinned],
  ] as const;
  return (
    <tr role="row">
      <td role="cell" className={styles.orgName}>
        {name}
        {name !== row.model && <span className={`${styles.statusNote} ${css.modelId}`}>{row.model}</span>}
        {job && <span className={styles.statusNote}>{job}</span>}
        {reads && (
          <span className={styles.statusNote}>
            {formatNumber(row.documents)} documents{line.tooFew ? `, too few (under ${BOARD_MIN_DOCUMENTS})` : ""}
          </span>
        )}
      </td>
      <td role="cell" data-label="Pages" className="num">
        {formatNumber(line.pages)}
        {reads && (
          <span className={styles.statusNote}>
            {formatNumber(row.pages_ai)} by AI, {formatNumber(row.pages_local)} here, {formatNumber(row.pages_cached)} cached
          </span>
        )}
      </td>
      <td role="cell" data-label="$ per 1,000 pages (est.)" className="num">
        {compare(usd(line.usdPer1000Pages, row.cost_estimated))}
        <Best word="lowest" on={best?.per1000} />
      </td>
      <td role="cell" data-label="Cache saved" className="num">
        {compare(cacheText(line.cachedShare))}
      </td>
      <td role="cell" data-label="Seconds per page" className="num">
        {compare(secondsText("typical", row.seconds_per_page_p50))}
        <Best word="fastest" on={best?.seconds} />
        {reads && !line.tooFew && <span className={styles.statusNote}>{secondsText("slowest 5%", row.seconds_per_page_p95)}</span>}
      </td>
      <td role="cell" data-label="Page signals" className="tabular">
        {!reads || line.tooFew ? (
          compare("")
        ) : (
          <ul className={css.flags}>
            {signals.map(([label, value]) => (
              <li key={label}>
                {label}: {signalText(value, row.ocr_pages)}
              </li>
            ))}
          </ul>
        )}
      </td>
      <td role="cell" data-label="Failed or without a card" className="num">
        {compare(`${rateText(row.runs_failed, row.runs)} failed`)}
        <Best word="fewest" on={best?.failed} />
        {reads && !line.tooFew && (
          <span className={styles.statusNote}>
            {rateText(row.runs_degraded, row.runs)} without a card, {`of ${formatNumber(row.runs)} runs`}
          </span>
        )}
      </td>
      <td role="cell" data-label="AI cost" className="num">
        {formatCost(row)}
        <Best word="lowest" on={best?.cost} />
      </td>
    </tr>
  );
}
