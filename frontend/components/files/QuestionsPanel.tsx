"use client";

// The document page's Questions (File management P2): the questions this person asked
// about this document, or whose answer cites any version of it, newest first, each with its
// answer and a link back to its conversation. Questions and answers are plain text.

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { NOT_FOUND_TEXT, UNCHECKED_TEXT } from "@/components/chat/answer";
import { ApiError, documentQuestions } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { Answer, DocumentQuestion } from "@/lib/types";
import styles from "@/components/activity/Activity.module.css";

export const QUESTIONS_TEXT = {
  heading: "Questions",
  empty: "No questions about this document yet. Ask about it, and your questions show here.",
  loading: "Loading your questions…",
  loadFailed: "Your questions did not load.",
  open: "Open the conversation",
  noAnswer: "No answer was saved.",
  tryAgain: "Try again",
};

export function questionAnswerText(answer: Answer | null): string {
  if (answer === null) return QUESTIONS_TEXT.noAnswer;
  if (answer.status === "not_found") return NOT_FOUND_TEXT;
  if (answer.status === "unchecked") return UNCHECKED_TEXT;
  return answer.sentences.map((sentence) => sentence.text).filter(Boolean).join(" ") || QUESTIONS_TEXT.noAnswer;
}

export function QuestionsPanel({ documentId, initial }: { documentId: string; initial?: DocumentQuestion[] }) {
  const [items, setItems] = useState<DocumentQuestion[] | null>(initial ?? null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (initial) return;
    let cancelled = false;
    documentQuestions(documentId).then(
      (reply) => {
        if (!cancelled) {
          setItems(reply.items);
          setError(null);
        }
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught instanceof ApiError ? caught.message : QUESTIONS_TEXT.loadFailed);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, attempt, initial]);

  if (error !== null && items === null) {
    return (
      <div className={styles.note} role="alert">
        <p>{error}</p>
        <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
          <Icon name="refresh" />
          {QUESTIONS_TEXT.tryAgain}
        </button>
      </div>
    );
  }
  if (items === null) {
    return (
      <p className="muted" role="status">
        {QUESTIONS_TEXT.loading}
      </p>
    );
  }
  if (items.length === 0) return <p className="muted">{QUESTIONS_TEXT.empty}</p>;
  return (
    <ol className={styles.timeline} aria-label={QUESTIONS_TEXT.heading}>
      {items.map((item) => (
        <li key={item.id} className={styles.row}>
          <span className={styles.what}>{item.question}</span>
          <span className={styles.meta}>{questionAnswerText(item.answer)}</span>
          <span className={styles.meta}>
            <time dateTime={item.asked_at}>{formatDateTime(item.asked_at)}</time>
            {" · "}
            <Link href={`/chat?c=${encodeURIComponent(item.conversation_id)}`}>{QUESTIONS_TEXT.open}</Link>
          </span>
        </li>
      ))}
    </ol>
  );
}
