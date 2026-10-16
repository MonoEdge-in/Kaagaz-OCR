"use client";

// A thin progress bar and "About 40 s left" beside a document being read (PRD 4 #13;
// the rules are in lib/progress.ts and lib/timeLeft.ts), the second line of its status
// cell. The bar is a native <progress> named for its document, the same width in every
// state; no percent is shown (changed on purpose, scan states, 16 Oct 2026). The
// counting text is hidden from screen readers, which get words that change only by the
// minute. No live region here: the status bar (UploadStatus) speaks for the uploads. A
// refresh starts the bar from the server's done_fraction.

import { memo, useState } from "react";
import { useMediaQuery } from "@/components/chat/hooks";
import { advance, barValue, documentFraction } from "@/lib/progress";
import type { ShownProgress } from "@/lib/progress";
import { ESTIMATING, STARTING, describe, hold, receivedAt } from "@/lib/timeLeft";
import type { Held } from "@/lib/timeLeft";
import { useTick } from "@/lib/tick";
import type { DocumentSummary, TimeLeft as TimeLeftValue } from "@/lib/types";
import styles from "./Documents.module.css";

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
      <progress className={`${styles.readMeter} ${styles.scanMeter}`} max={100} value={barValue(next.percent)} aria-label={`Progress: ${name}`} />
      <Words text={view.text} spoken={view.spoken} />
    </>
  );
}

// Renders again only when the words change, not on every tick. The same words for eyes and
// ears are written once, so a copy or a text read-out never says the same words twice.
const Words = memo(function Words({ text, spoken }: { text: string; spoken: string }) {
  if (text === spoken) return <span className={styles.statusText} title={text}>{text}</span>;
  return (
    <span className={styles.statusText} title={text}>
      <span aria-hidden="true">{text}</span>
      <span className="visually-hidden">{spoken}</span>
    </span>
  );
});
