"use client";

// "Ready in about 40 s" beside a document being read (PRD 4 #13; the rules are in
// lib/timeLeft.ts). The counting text is hidden from screen readers, which get
// words that change only by the minute. No live region: nothing is announced.

import { memo, useState } from "react";
import { useMediaQuery } from "@/components/chat/hooks";
import { WAITING, describe, hold, receivedAt } from "@/lib/timeLeft";
import type { Held } from "@/lib/timeLeft";
import { useTick } from "@/lib/tick";
import type { DocumentSummary, TimeLeft as TimeLeftValue } from "@/lib/types";
import styles from "./Documents.module.css";

export function TimeLeft({ document }: { document: Pick<DocumentSummary, "status" | "progress" | "time_left"> }) {
  if (document.time_left && document.progress) {
    return <Countdown value={document.time_left} step={document.progress.step} />;
  }
  const waiting = document.status === "received" && document.progress === null;
  return waiting ? <Words text={WAITING.text} spoken={WAITING.spoken} /> : null;
}

function Countdown({ value, step }: { value: TimeLeftValue; step: Held["step"] }) {
  const now = useTick();
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const [held, setHeld] = useState<Held | null>(null);
  let current = held;
  if (current === null || current.value !== value) {
    // A new answer, kept from render to render ("storing information from previous renders").
    current = hold(held, value, step, receivedAt(value));
    setHeld(current);
  }
  const view = describe(current, now, reducedMotion);
  return <Words text={view.text} spoken={view.spoken} />;
}

// Renders again only when the words change, not on every tick.
const Words = memo(function Words({ text, spoken }: { text: string; spoken: string }) {
  return (
    <span className={styles.timeLeft}>
      <span aria-hidden="true">{text}</span>
      <span className="visually-hidden">{spoken}</span>
    </span>
  );
});
