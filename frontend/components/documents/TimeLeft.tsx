"use client";

// A progress bar with "42%" and "Ready in about 40 s" beside a document being read
// (PRD 4 #13; the rules are in lib/progress.ts and lib/timeLeft.ts). The bar is a
// native <progress> named for its document; the visible percent and the counting
// text are hidden from screen readers, which get words that change only by the
// minute. No live region: nothing is announced. A refresh starts the bar from the
// server's done_fraction, so "36%" stays beside "Taking longer than usual"; at 0 no
// percent text is shown (never a bare 0%).

import { memo, useState } from "react";
import { useMediaQuery } from "@/components/chat/hooks";
import { advance, barValue, documentFraction } from "@/lib/progress";
import type { ShownProgress } from "@/lib/progress";
import { ESTIMATING, STARTING, describe, hold, receivedAt } from "@/lib/timeLeft";
import type { Held } from "@/lib/timeLeft";
import { useTick } from "@/lib/tick";
import type { DocumentSummary, TimeLeft as TimeLeftValue } from "@/lib/types";
import styles from "./Documents.module.css";
import { formatProgressPercent } from "./logic";

type Shown = Pick<DocumentSummary, "status" | "progress" | "time_left" | "attempts" | "deleted_at" | "display_name">;

export function TimeLeft({ document }: { document: Shown }) {
  const { progress } = document;
  if (progress && document.deleted_at === null) {
    // A new attempt starts a new bar and countdown (keyed), from that run's done_fraction.
    return (
      <Progressing
        key={document.attempts}
        value={document.time_left}
        step={progress.step}
        attempt={document.attempts}
        name={document.display_name}
      />
    );
  }
  // With no progress the status message says what it waits for (api/views.py: "Waiting to
  // start.", "Waiting to try again." and the like), so nothing more is shown here.
  return null;
}

interface ProgressingProps {
  value: TimeLeftValue | null; // null while scanning, or with no estimate
  step: Held["step"];
  attempt: number;
  name: string;
}

function Progressing({ value, step, attempt, name }: ProgressingProps) {
  const now = useTick();
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [held, setHeld] = useState<Held | null>(null);
  const [shown, setShown] = useState<ShownProgress | null>(null);
  let current: Held | null = null;
  if (value !== null) {
    current = held;
    if (current === null || current.value !== value) {
      // A new answer, kept from render to render ("storing information from previous renders").
      current = hold(held, value, step, receivedAt(value));
      setHeld(current);
    }
  }
  const scanning = step === "scanning";
  const view = current !== null ? describe(current, now, reducedMotion) : scanning ? STARTING : ESTIMATING;
  // The same fraction as its upload row and its batch's bar (documentFraction).
  const next = advance(shown, attempt, documentFraction(step, value, now));
  if (next !== shown) setShown(next);
  return (
    <>
      <span className={styles.readBar}>
        <progress
          className={styles.readMeter}
          max={100}
          value={barValue(next.percent)}
          aria-label={`Progress: ${name}`}
        />
        <span className={styles.readPercent} aria-hidden="true">
          {next.percent > 0 ? formatProgressPercent(next.percent, 100) : ""}
        </span>
      </span>
      <Words text={view.text} spoken={view.spoken} />
    </>
  );
}

// Renders again only when the words change, not on every tick. The same words for eyes and
// ears are written once, so a copy or a text read-out never says the same words twice.
const Words = memo(function Words({ text, spoken }: { text: string; spoken: string }) {
  if (text === spoken) return <span className={styles.timeLeft}>{text}</span>;
  return (
    <span className={styles.timeLeft}>
      <span aria-hidden="true">{text}</span>
      <span className="visually-hidden">{spoken}</span>
    </span>
  );
});
