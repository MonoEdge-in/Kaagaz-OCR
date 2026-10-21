// One file's way in (Components.html, 13): Sending → Saving → Reading, never
// 100% early. Reading takes half the bar and only fills when the last page is
// read. Sending has a real percentage (the upload's own progress) and Reading
// a real page count; Saving has no number to show, so its segment moves
// without one instead of making one up. FiledPath is where a ready file went.

import type { CSSProperties, ReactNode } from "react";
import { formatNumber } from "@/lib/format";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import styles from "./StageProgress.module.css";

export type UploadStage =
  | { stage: "sending"; percent: number }
  | { stage: "saving" }
  /** total 0: a spreadsheet, read as a whole. */
  | { stage: "reading"; done: number; total: number };

/**
 * The whole bar's progress, 0 to 99, weighted as it is drawn: a quarter for
 * sending, a quarter for saving, half for reading. Never 100: a file is done
 * when it is ready, and then the bar is gone.
 */
export function overallPercent(progress: UploadStage): number {
  const clamp = (n: number) => Math.min(1, Math.max(0, n));
  let value: number;
  if (progress.stage === "sending") value = 25 * clamp(progress.percent / 100);
  else if (progress.stage === "saving") value = 25;
  else value = 50 + (progress.total > 0 ? 50 * clamp(progress.done / progress.total) : 0);
  return Math.min(99, Math.floor(value));
}

/** What the file is doing, in words: "Sending 60%", "Saving", "Reading 4 of 12 pages". */
export function stageWords(progress: UploadStage): string {
  if (progress.stage === "sending") return `Sending ${Math.round(progress.percent)}%`;
  if (progress.stage === "saving") return "Saving";
  if (progress.total === 0) return "Reading";
  return `Reading ${formatNumber(progress.done)} of ${formatNumber(progress.total)} pages`;
}

function Segment({ active, fill }: { active?: boolean; fill?: number | "moving" }) {
  return (
    <span className={cx(styles.stage, active && styles.active, fill === "moving" && styles.moving)}>
      {fill === undefined ? null : (
        <i style={fill === "moving" ? undefined : ({ width: `${Math.round(Math.min(100, Math.max(0, fill)) * 10) / 10}%` } as CSSProperties)} />
      )}
    </span>
  );
}

export function StageProgress({ name, progress }: { name: ReactNode; progress: UploadStage }) {
  const words = stageWords(progress);
  const reading = progress.stage === "reading";
  const readFill = reading ? (progress.total > 0 ? (100 * progress.done) / progress.total : "moving") : undefined;
  return (
    <div className={styles.file}>
      <div className={styles.top}>
        <span className={styles.name}>{name}</span>
        <span className="k-small k-muted">{words}</span>
      </div>
      <div
        className={styles.stages}
        role="progressbar"
        aria-label={words}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={overallPercent(progress)}
      >
        <Segment active={progress.stage === "sending"} fill={progress.stage === "sending" ? progress.percent : 100} />
        <Segment active={progress.stage === "saving"} fill={progress.stage === "saving" ? "moving" : reading ? 100 : undefined} />
        <Segment active={reading} fill={readFill} />
      </div>
      <div className={styles.labels} aria-hidden="true">
        {progress.stage === "sending" ? <span className={styles.active}>Sending</span> : <span className={styles.done}>Sent</span>}
        {progress.stage === "saving" ? (
          <span className={styles.active}>Saving</span>
        ) : reading ? (
          <span className={styles.done}>Saved</span>
        ) : (
          <span>Saving</span>
        )}
        {reading ? (
          <span className={styles.active}>
            {progress.total > 0 ? `Reading ${formatNumber(progress.done)} / ${formatNumber(progress.total)}` : "Reading"}
          </span>
        ) : (
          <span>Reading</span>
        )}
      </div>
    </div>
  );
}

/** Where a ready file was filed: "Accounts / Invoices / Mehta Textiles". */
export function FiledPath({ children }: { children: ReactNode }) {
  return (
    <span className={styles.filed}>
      <Icon name="arrow-right" size="sm" />
      <b>{children}</b>
    </span>
  );
}
