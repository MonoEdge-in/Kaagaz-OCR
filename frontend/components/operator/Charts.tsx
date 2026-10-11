// The Overview's and Quality's small pictures (operator insights, council 11 Oct 2026):
// a sparkline and a share bar. Both are hidden from screen readers: the number or the
// legend next to them says the same in words. Never canvas, never role="img"; colours are
// theme tokens only, so dark mode and forced colours keep working.

import type { ReactElement } from "react";
import { formatNumber } from "@/lib/format";

const WIDTH = 100;
const HEIGHT = 24;
const PAD = 1; // keeps the stroke inside the box at 0 and at the top

const coord = (n: number) => Math.round(n * 100) / 100;

/** A day series as a line, from 0 (never from its smallest value); nothing or all 0 is a flat baseline. */
export function Sparkline({ values }: { values: readonly number[] }): ReactElement {
  const clean = values.map((value) => (Number.isFinite(value) && value > 0 ? value : 0));
  const top = Math.max(0, ...clean);
  const y = (value: number) => coord(HEIGHT - PAD - (top > 0 ? (value / top) * (HEIGHT - 2 * PAD) : 0));
  const points =
    clean.length < 2
      ? `0,${y(clean[0] ?? 0)} ${WIDTH},${y(clean[0] ?? 0)}`
      : clean.map((value, i) => `${coord((i / (clean.length - 1)) * WIDTH)},${y(value)}`).join(" ");
  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
      width="100%"
      height={HEIGHT}
    >
      <polyline
        points={points}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.5}
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export interface SharePart { label: string; value: number }

// Theme tokens, light and dark (app/globals.css); the legend's words carry the meaning.
const TOKENS = ["--accent", "--verified-solid", "--review-solid", "--fg-muted", "--danger-fg", "--border-strong"];

function shareWords(value: number, total: number): string {
  const percent = (value / total) * 100;
  return percent > 0 && percent < 1 ? "< 1%" : `${formatNumber(Math.round(percent))}%`;
}

/** Parts of a whole as one bar of CSS segments plus a text legend of every part ("Label 71%"); a zero total is a note. */
export function ShareBar({ parts }: { parts: readonly SharePart[] }): ReactElement {
  const legend = parts.map((part, i) => ({
    ...part,
    value: Number.isFinite(part.value) && part.value > 0 ? part.value : 0,
    colour: `var(${TOKENS[i % TOKENS.length]})`,
  }));
  const total = legend.reduce((sum, part) => sum + part.value, 0);
  if (total === 0) return <p>Nothing in this period.</p>;
  return (
    <div>
      {/* forcedColorAdjust keeps the segments drawn in forced colours; the legend says the same in words */}
      <div
        aria-hidden="true"
        style={{ display: "flex", height: "0.75rem", borderRadius: "var(--r-sm)", overflow: "hidden", forcedColorAdjust: "none" }}
      >
        {legend.filter((part) => part.value > 0).map((part) => (
          <span key={part.label} style={{ width: `${coord((part.value / total) * 100)}%`, background: part.colour }} />
        ))}
      </div>
      <ul style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-1) var(--space-4)", listStyle: "none", padding: 0, margin: "var(--space-2) 0 0" }}>
        {legend.map((part) => (
          <li key={part.label}>
            <span
              aria-hidden="true"
              style={{ display: "inline-block", width: "0.75rem", height: "0.75rem", marginInlineEnd: "var(--space-1)", borderRadius: "var(--r-sm)", background: part.colour, forcedColorAdjust: "none" }}
            />
            {part.label} {shareWords(part.value, total)}
          </li>
        ))}
      </ul>
    </div>
  );
}
