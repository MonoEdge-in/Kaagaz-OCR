// The Ask conversation's parts (Components.html, 14). AnswerCard: the answer's
// status badge and where it came from, then the answer in reading size, its
// sentences carrying CitationChips. Question: the asked question, on the
// right. Working: the one line shown while an answer is on its way, with the
// time so far; until the server streams real steps (NB-23) it never shows a
// step, so it never shows one that didn't happen. SearchingFor: what a
// follow-up was searched as.

import type { ReactNode } from "react";
import type { UiStatus } from "@/lib/status";
import { StatusBadge } from "../StatusBadge/StatusBadge";
import styles from "./AnswerCard.module.css";

export interface AnswerCardProps {
  status: UiStatus;
  /** "from 2 pages in 1 document". */
  meta?: ReactNode;
  children: ReactNode;
  /** Under the answer: its source cards, Copy, Helpful. */
  footer?: ReactNode;
}

export function AnswerCard({ status, meta, children, footer }: AnswerCardProps) {
  return (
    <article className={styles.answer}>
      <div className={styles.head}>
        <StatusBadge state={status} />
        {meta ? <span className="k-small k-muted">{meta}</span> : null}
      </div>
      <div className="k-read">{children}</div>
      {footer}
    </article>
  );
}

export function Question({ children }: { children: ReactNode }) {
  return <p className={styles.question}>{children}</p>;
}

/** "1:05", "0:07": minutes and seconds. */
export function elapsedWords(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

export function Working({ seconds }: { seconds: number }) {
  return (
    <p className={styles.working} role="status">
      <span className={styles.dots} aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      Working on it
      <span className={styles.elapsed}>{elapsedWords(seconds)}</span>
    </p>
  );
}

export function SearchingFor({ children }: { children: ReactNode }) {
  return <span className={styles.searching}>Searching for: {children}</span>;
}
