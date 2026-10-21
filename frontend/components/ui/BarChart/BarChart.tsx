// Counts as horizontal bars ("Documents by type" on Home): the name, a bar as
// long as its share of the biggest, and the number. It is a description list,
// so the names and numbers are read out; the bars are only drawn.

import type { CSSProperties } from "react";
import { formatNumber } from "@/lib/format";
import styles from "./BarChart.module.css";

export interface Bar {
  label: string;
  value: number;
}

/** A bar's length, as a share of the biggest: 0 to 100. */
export function barWidth(value: number, max: number): number {
  if (max <= 0 || value <= 0) return 0;
  return Math.min(100, Math.round((value / max) * 1000) / 10);
}

export function BarChart({ label, bars }: { label: string; bars: Bar[] }) {
  const max = Math.max(0, ...bars.map((bar) => bar.value));
  return (
    <dl className={styles.bars} aria-label={label}>
      {bars.map((bar) => (
        <div key={bar.label} className={styles.bar}>
          <dt>{bar.label}</dt>
          <dd>
            <span className={styles.track} aria-hidden="true">
              <span className={styles.fill} style={{ width: `${barWidth(bar.value, max)}%` } as CSSProperties} />
            </span>
            <span className={styles.value}>{formatNumber(bar.value)}</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}
