// A batch at a glance (Components.html, 13): one bar split by what each file is
// doing, and a legend with the words and counts, so the split reads without
// colour too (the reading part is also striped). Only parts with files show.

import type { CSSProperties } from "react";
import { formatNumber } from "@/lib/format";
import styles from "./BatchMeter.module.css";

export type BatchPart = "ready" | "reading" | "waiting" | "dup" | "attention" | "failed";

const WORDS: Record<BatchPart, string> = {
  ready: "Ready",
  reading: "Reading",
  waiting: "Waiting",
  dup: "Already uploaded",
  attention: "Needs attention",
  failed: "Failed",
};
const ORDER: BatchPart[] = ["ready", "reading", "waiting", "dup", "attention", "failed"];

export type BatchCounts = Partial<Record<BatchPart, number>>;

/** The parts with files, in order, each with its share of the bar (0 to 100). */
export function meterParts(counts: BatchCounts): { part: BatchPart; count: number; width: number }[] {
  const total = ORDER.reduce((sum, part) => sum + (counts[part] ?? 0), 0);
  if (total === 0) return [];
  return ORDER.filter((part) => (counts[part] ?? 0) > 0).map((part) => ({
    part,
    count: counts[part]!,
    width: Math.round(((counts[part]! / total) * 100) * 10) / 10,
  }));
}

/** "41 ready, 5 reading, 2 waiting, 1 already uploaded". */
export function meterWords(counts: BatchCounts): string {
  return meterParts(counts)
    .map(({ part, count }) => `${formatNumber(count)} ${WORDS[part].toLowerCase()}`)
    .join(", ");
}

export function BatchMeter({ counts }: { counts: BatchCounts }) {
  const parts = meterParts(counts);
  return (
    <>
      <div className={styles.meter} role="img" aria-label={meterWords(counts) || "No files yet"}>
        {parts.map(({ part, width }) => (
          <span key={part} className={styles[part]} style={{ width: `${width}%` } as CSSProperties} />
        ))}
      </div>
      <ul className={styles.legend}>
        {parts.map(({ part, count }) => (
          <li key={part}>
            <i className={styles[part]} />
            {WORDS[part]} {formatNumber(count)}
          </li>
        ))}
      </ul>
    </>
  );
}
