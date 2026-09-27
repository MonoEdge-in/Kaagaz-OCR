"use client";

// One answer: its sentences with citation numbers (each a button that moves to
// its source card), the label in words, and the source cards. "Not found" is a
// real answer: it says so and lists the closest documents. All text is shown as
// plain text; answers are built from document text, which is untrusted.

import Link from "next/link";
import { labelBadgeClass } from "@/components/viewer/labels";
import type { Message } from "@/lib/types";
import {
  answerLabelText,
  cardId,
  citationMap,
  NOT_FOUND_TEXT,
  sentenceCitations,
  understoodAs,
} from "./answer";
import { FeedbackControls } from "./FeedbackControls";
import { SourceCard } from "./SourceCard";
import styles from "./Chat.module.css";

function focusCard(id: string) {
  const card = window.document.getElementById(id);
  if (card) card.focus();
}

export function AnswerView({ message, question }: { message: Message; question: string | null }) {
  const answer = message.answer;
  if (!answer) return <p className={styles.plain}>{message.text}</p>;

  const reading = question !== null ? understoodAs(question, answer.normalized_question) : null;
  const understood = reading ? <p className="muted">Understood as: {reading}</p> : null;

  if (answer.status === "not_found") {
    return (
      <div className="stack">
        {understood}
        <p className={styles.plain}>{NOT_FOUND_TEXT}</p>
        {answer.closest_documents.length > 0 ? (
          <div>
            <p className="muted">The closest documents:</p>
            <ul>
              {answer.closest_documents.map((doc) => (
                <li key={doc.id}>
                  <Link href={`/documents/${encodeURIComponent(doc.id)}`}>{doc.display_name}</Link>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <FeedbackControls messageId={message.id} initial={message.feedback} />
      </div>
    );
  }

  const cards = citationMap(answer);
  const label = answer.label ? answerLabelText(answer.label) : null;

  return (
    <div className="stack">
      {understood}
      <p className={styles.plain}>
        {answer.sentences.map((sentence, index) => (
          <span key={index}>
            {index > 0 ? " " : null}
            {sentence.text}
            {sentenceCitations(sentence, cards).map((n) => (
              <sup key={n}>
                <button
                  type="button"
                  className={styles.cite}
                  aria-label={`Source ${n}`}
                  onClick={() => focusCard(cardId(message.id, n))}
                >
                  {n}
                </button>
              </sup>
            ))}
          </span>
        ))}
      </p>
      {label && answer.label ? (
        <p className="row">
          <span className={labelBadgeClass(answer.label)}>
            {label.title}
          </span>
          <span className="muted">{label.explanation}</span>
        </p>
      ) : null}
      {answer.citations.length > 0 ? (
        <section className="stack" aria-label="Sources">
          {answer.citations.map((citation) => (
            <SourceCard key={citation.n} citation={citation} messageId={message.id} />
          ))}
        </section>
      ) : null}
      <FeedbackControls messageId={message.id} initial={message.feedback} />
    </div>
  );
}
