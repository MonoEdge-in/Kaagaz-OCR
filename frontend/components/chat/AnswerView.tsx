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
// read. An answer more than one model was asked to draft (features 25-27) says who
// answered and whether they agreed, under its label. All text is plain text; answers
// are built from document text, which is untrusted.
// UI audit A4 (16 Oct 2026): "not found" ends with calm next steps, never a dead end:
// search all documents (the newest answer only, when it searched fewer: onAskAll),
// ask with the words the document uses, teach chat a word, or upload the document.

import Link from "next/link";
import { memo, useState } from "react";
import type React from "react";
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
  teamLine,
} from "./answer";
import { FeedbackControls } from "./FeedbackControls";
import { scrollBehavior } from "./hooks";
import { SourceCard } from "./SourceCard";
import styles from "./Chat.module.css";

/** Bring the source card into view (only as far as needed) and move focus to it,
 * opening its Sources bar first when it is closed. */
function goToSource(id: string) {
  const card = window.document.getElementById(id);
  if (!card) return;
  const bar = card.closest("details");
  if (bar && !bar.open) bar.open = true;
  card.scrollIntoView({ block: "nearest", behavior: scrollBehavior() });
  card.focus({ preventScroll: true });
}

/**
 * One collapsible bar holding an answer's sources (Tushar, 16 Oct 2026): closed at
 * first, its summary saying what is inside ("Sources · 4", "Sources · 20 pages to
 * check · 3 documents"). A citation number opens it (goToSource).
 */
function SourcesBar({ count, children }: { count: string; children: React.ReactNode }) {
  return (
    <details className={styles.sourcesBar}>
      <summary className={styles.sourcesSummary}>
        <Icon name="chevron-right" size={16} className={styles.sourcesChevron} />
        <span className={styles.sourcesLabel}>Sources</span>
        <span className={styles.sourcesCount}>{count}</span>
      </summary>
      <div className={styles.sourcesBody}>{children}</div>
    </details>
  );
}

const counted = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

// Memo: the chat keeps each message object as it was when a new answer arrives,
// so old answer cards skip re-rendering (UI quality council, 15 Oct 2026).
export const AnswerView = memo(function AnswerView({
  message,
  question,
  onAskAll,
}: {
  message: Message;
  question: string | null;
  onAskAll?: () => void;
}) {
  // The source a chip is pointed at or focused: its card lights up (UI quality council, 15 Oct 2026).
  const [hot, setHot] = useState<number | null>(null);
  const answer = message.answer;

  if (!answer) {
    return (
      <div className={`motion-rise ${styles.answerCard}`}>
        <span className="visually-hidden">Answer: </span>
        <p className={styles.plain}>{message.text}</p>
      </div>
    );
  }

  const reading = questionReading(question, answer);
  const note = readingNote(answer);
  const noteText = note ? <p className={styles.plain}>{note.text}</p> : null;
  const pageCount = note ? note.pages.length + note.more : 0;
  const notePages =
    note && note.pages.length > 0 ? (
      <div className={styles.readingNote}>
        <h3 className={styles.subheading}>Pages to check</h3>
        <ul className={styles.docChips} aria-label="Pages to check">
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
      <div className={`motion-rise ${styles.answerCard}`}>
        <span className="visually-hidden">Answer: </span>
        <div className={styles.notFound}>
          <span className={styles.notFoundIcon}>
            <Icon name="search-off" size={20} />
          </span>
          <p className={styles.plain}>{NOT_FOUND_TEXT}</p>
        </div>
        {noteText}
        {understood}
        {notePages || answer.closest_documents.length > 0 ? (
          <SourcesBar
            count={[
              pageCount > 0 ? counted(pageCount, "page to check", "pages to check") : "",
              answer.closest_documents.length > 0
                ? counted(answer.closest_documents.length, "document", "documents")
                : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          >
            {notePages}
            {documentChips("Closest documents")}
          </SourcesBar>
        ) : null}
        <div>
          <h3 className={styles.subheading}>Next steps</h3>
          <ul className={styles.nextSteps}>
            {onAskAll ? (
              <li>
                <button type="button" className="btn" onClick={onAskAll}>
                  <Icon name="search" size={16} />
                  Search all documents
                </button>
              </li>
            ) : null}
            <li>Ask again with a name, number or date as the document writes it.</li>
            <li>
              Write a word your own way? <Link href="/words">Add it to My words</Link>.
            </li>
            <li>
              Not uploaded yet? <Link href="/documents">Upload the document</Link>.
            </li>
          </ul>
        </div>
        {feedback}
      </div>
    );
  }

  // The figure is only in unchecked card values: no number, a request to check it
  // in Review first, and the documents to open and read (decided 30 Sep 2026).
  if (answer.status === "unchecked") {
    return (
      <div className={`motion-rise ${styles.answerCard}`} data-label="needs_review">
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
  const team = teamLine(answer);

  return (
    <div className={`motion-rise ${styles.answerCard}`} data-label={label ?? undefined}>
      <span className="visually-hidden">Answer: </span>
      {label ? (
        <div className={styles.labelLine}>
          <LabelBadge label={label} />
          <span className={styles.labelExplanation}>{answerLabelText(label, answer).explanation}</span>
        </div>
      ) : null}
      {team ? <p className={styles.understood}>{team}</p> : null}
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
                onPointerEnter={() => setHot(n)}
                onPointerLeave={() => setHot(null)}
                onFocus={() => setHot(n)}
                onBlur={() => setHot(null)}
              >
                {n}
              </button>
            ))}
          </span>
        ))}
      </p>
      {noteText}
      {answer.citations.length > 0 || notePages ? (
        <SourcesBar
          count={[
            answer.citations.length > 0 ? String(answer.citations.length) : "",
            pageCount > 0 ? counted(pageCount, "page to check", "pages to check") : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        >
          {answer.citations.length > 0 ? (
            <div className={styles.sources}>
              {answer.citations.map((citation) => (
                <SourceCard key={citation.n} citation={citation} messageId={message.id} hot={hot === citation.n} />
              ))}
            </div>
          ) : null}
          {notePages}
        </SourcesBar>
      ) : null}
      {feedback}
    </div>
  );
});
