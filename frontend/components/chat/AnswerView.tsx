"use client";

// One answer card. In order: the label with a line saying what it means, what
// a follow-up was searched for ("Searching for: …", PRD 3 H1) and how the
// question was understood (only when that differs), the sentences with
// citation chips (each moves to its source card), the source cards, and
// "Was this helpful?". The card's inline-start edge carries the margin rule:
// solid for Verified, hatched for Needs review. "Not found" is a real answer:
// a neutral card that says so and lists the closest documents. "Unchecked" says
// a figure is only in values nobody has checked yet, and where. F6's note
// (PRD 5 U7) goes under the answer, with a link to each page that couldn't be
// read. All text is plain text; answers are built from document text, which is untrusted.

import Link from "next/link";
import { useId } from "react";
import { Icon } from "@/components/ui/Icon";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { understoodNotes } from "@/components/words/words";
import type { Message } from "@/lib/types";
import {
  answerLabelText,
  cardId,
  citationMap,
  NOT_FOUND_TEXT,
  questionReading,
  readingNote,
  sentenceCitations,
} from "./answer";
import { FeedbackControls } from "./FeedbackControls";
import { scrollBehavior } from "./hooks";
import { SourceCard } from "./SourceCard";
import styles from "./Chat.module.css";

/** Bring the source card into view (only as far as needed) and move focus to it. */
function goToSource(id: string) {
  const card = window.document.getElementById(id);
  if (!card) return;
  card.scrollIntoView({ block: "nearest", behavior: scrollBehavior() });
  card.focus({ preventScroll: true });
}

export function AnswerView({ message, question }: { message: Message; question: string | null }) {
  const sourcesHeadingId = useId();
  const answer = message.answer;

  if (!answer) {
    return (
      <div className={styles.answerCard}>
        <span className="visually-hidden">Answer: </span>
        <p className={styles.plain}>{message.text}</p>
      </div>
    );
  }

  const reading = questionReading(question, answer);
  const note = readingNote(answer);
  const noteBlock = note ? (
    <div className={styles.readingNote}>
      <p className={styles.plain}>{note.text}</p>
      {note.pages.length > 0 && (
        <ul className={styles.docChips} aria-label="Pages that couldn't be read">
          {note.pages.map((page) => (
            <li key={page.href}>
              <Link className={styles.docChip} href={page.href}>
                <Icon name="open" size={16} />
                {page.text}
              </Link>
            </li>
          ))}
          {note.more > 0 && <li className={styles.understood}>and {note.more} more</li>}
        </ul>
      )}
    </div>
  ) : null;
  // My words chat used (A3 sends understood_words; nothing shows before it). Text only.
  const notes = understoodNotes(answer);
  const understood = (
    <>
      {reading.searchingFor ? (
        <p className={styles.understood}>Searching for: {reading.searchingFor}</p>
      ) : null}
      {reading.understoodAs ? (
        <p className={styles.understood}>Understood as: {reading.understoodAs}</p>
      ) : null}
      {notes.length > 0 ? (
        <p className={styles.understood}>
          {notes.join(". ")}. <Link href="/words">My words</Link>
        </p>
      ) : null}
    </>
  );
  const feedback = (
    <div className={styles.answerFooter}>
      <FeedbackControls messageId={message.id} initial={message.feedback} />
    </div>
  );

  const documentChips = (heading: string) =>
    answer.closest_documents.length > 0 ? (
      <div>
        <h3 className={styles.subheading}>{heading}</h3>
        <ul className={styles.docChips}>
          {answer.closest_documents.map((doc) => (
            <li key={doc.id}>
              <Link className={styles.docChip} href={`/documents/${encodeURIComponent(doc.id)}`}>
                <Icon name="file" size={16} />
                <span className={styles.docChipName}>{doc.display_name}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    ) : null;

  if (answer.status === "not_found") {
    return (
      <div className={styles.answerCard}>
        <span className="visually-hidden">Answer: </span>
        <div className={styles.notFound}>
          <span className={styles.notFoundIcon}>
            <Icon name="search-off" size={20} />
          </span>
          <p className={styles.plain}>{NOT_FOUND_TEXT}</p>
        </div>
        {noteBlock}
        {understood}
        {documentChips("Closest documents")}
        {feedback}
      </div>
    );
  }

  // The figure is only in unchecked card values: no number, a request to check it
  // in Review first, and the documents to open and read (decided 30 Sep 2026).
  if (answer.status === "unchecked") {
    return (
      <div className={styles.answerCard} data-label="needs_review">
        <span className="visually-hidden">Answer: </span>
        <div className={styles.notFound}>
          <span className={styles.notFoundIcon}>
            <Icon name="eye-alert" size={20} />
          </span>
          {answer.sentences.map((sentence, index) => (
            <p key={index} className={styles.plain}>
              {sentence.text}
            </p>
          ))}
        </div>
        {understood}
        {documentChips("Where it appears")}
        {feedback}
      </div>
    );
  }

  const cards = citationMap(answer);
  const label = answer.label;

  return (
    <div className={styles.answerCard} data-label={label ?? undefined}>
      <span className="visually-hidden">Answer: </span>
      {label ? (
        <div className={styles.labelLine}>
          <LabelBadge label={label} />
          <span className={styles.labelExplanation}>{answerLabelText(label).explanation}</span>
        </div>
      ) : null}
      {understood}
      <p className={styles.answerText}>
        {answer.sentences.map((sentence, index) => (
          <span key={index}>
            {index > 0 ? " " : null}
            {sentence.text}
            {sentenceCitations(sentence, cards).map((n) => (
              <button
                key={n}
                type="button"
                className={`${styles.numberMark} ${styles.cite}`}
                aria-label={`Source ${n}`}
                onClick={() => goToSource(cardId(message.id, n))}
              >
                {n}
              </button>
            ))}
          </span>
        ))}
      </p>
      {noteBlock}
      {answer.citations.length > 0 ? (
        <section aria-labelledby={sourcesHeadingId}>
          <h3 id={sourcesHeadingId} className={styles.subheading}>
            Sources ({answer.citations.length})
          </h3>
          <div className={styles.sources}>
            {answer.citations.map((citation) => (
              <SourceCard key={citation.n} citation={citation} messageId={message.id} />
            ))}
          </div>
        </section>
      ) : null}
      {feedback}
    </div>
  );
}
