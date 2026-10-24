"use client";

// One answer card. In order: what a follow-up was searched for ("Searching for: …",
// PRD 3 H1) and how the question was understood (only when that differs), the
// sentences with citation chips (each moves to its source card), the source cards,
// and Useful or Not useful (PRD 6 F1). No Verified or Needs review label, badge or edge in Ask
// (Tushar, 16 Oct 2026): checking values is the Review screen's, kept apart.
// "Not found" is a real answer: a neutral card that says so and lists the closest
// documents. "Unchecked" (a figure only in values not confirmed yet) gives no number,
// only where it appears (UNCHECKED_TEXT). F6's note (PRD 5 U7) goes under the answer,
// with a link to each page that couldn't be read. All text is plain text; answers are
// built from document text, which is untrusted.
// Compact, ranked sources (Tushar, 16 Oct 2026; SourceCard.tsx): the Sources bar lists
// the cited sources most relevant first (the search's rank; the numbers in the text stay
// as written), then "Also in", the documents the search found but the answer does not
// cite. "Most relevant" on a row puts it on top at once, and it is kept for this answer
// (pinSource; the server's newest pick comes back in message.pin). One model answers
// in Ask (Tushar, 16 Oct 2026): no line on who answered or whether models agreed.
// "Also mentions “Jalna”" (Tushar, 17 Oct 2026; MentionsSection): last in the bar, every
// other document holding the question's key terms, and "Mentioned in N more documents".
// UI audit A4 (16 Oct 2026): "not found" ends with calm next steps, never a dead end:
// search all documents (the newest answer only, when it searched fewer: onAskAll),
// ask with the words the document uses, teach chat a word, or upload the document.

import Link from "next/link";
import { memo, useState } from "react";
import type React from "react";
import { Icon } from "@/components/ui/Icon";
import { understoodNotes } from "@/components/words/words";
import { pinSource } from "@/lib/api";
import type { Message, SourcePin } from "@/lib/types";
import {
  cardId,
  citationMap,
  isPinned,
  matchTerms,
  mentionsOf,
  NOT_FOUND_TEXT,
  questionReading,
  rankedRelated,
  rankedSources,
  readingNote,
  sentenceCitations,
  sourcesCount,
  UNCHECKED_TEXT,
} from "./answer";
import { FeedbackControls } from "./FeedbackControls";
import { scrollBehavior } from "./hooks";
import { DocumentRow, MentionsSection, RelatedRow, SourceCard } from "./SourceCard";
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
 * first, its summary saying what is inside ("Sources · 4 · Also in 3 documents",
 * "Sources · 20 pages to check · 3 documents"). A citation number opens it (goToSource).
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
  // The source put on top: the saved pick, then this person's, at once (the server keeps it).
  const [pin, setPin] = useState<SourcePin | null>(message.pin ?? null);
  const [pinFailed, setPinFailed] = useState(false);
  const answer = message.answer;
  const putOnTop = (target: SourcePin, previousRank: number) => {
    const before = pin;
    setPin(target);
    setPinFailed(false);
    pinSource(message.id, target, previousRank).catch(() => {
      setPin(before);
      setPinFailed(true);
    });
  };

  if (!answer) {
    return (
      <div className={`motion-rise ${styles.answerCard}`}>
        <span className="visually-hidden">Answer: </span>
        <p className={styles.plain}>{message.text}</p>
      </div>
    );
  }

  const reading = questionReading(question, answer);
  const mentions = mentionsOf(answer);
  const mentioned = mentions ? <MentionsSection mentions={mentions} /> : null;
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

  const documentRows = (heading: string) =>
    answer.closest_documents.length > 0 ? (
      <div>
        <h3 className={styles.subheading}>{heading}</h3>
        <ul className={styles.sources}>
          {answer.closest_documents.map((doc) => (
            <li key={doc.id}>
              <DocumentRow id={doc.id} name={doc.display_name} />
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
        {notePages || answer.closest_documents.length > 0 || mentions ? (
          <SourcesBar
            count={[
              pageCount > 0 ? counted(pageCount, "page to check", "pages to check") : "",
              answer.closest_documents.length > 0
                ? counted(answer.closest_documents.length, "document", "documents")
                : "",
              mentions ? `Mentioned in ${counted(mentions.total, "document", "documents")}` : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          >
            {notePages}
            {documentRows("Closest documents")}
            {mentioned}
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

  // The figure is only in card values not confirmed yet: no number, only the
  // documents to open and read (decided 30 Sep 2026). One calm line, also for answers
  // saved before 16 Oct 2026, whose sentence asked for Review.
  if (answer.status === "unchecked") {
    return (
      <div className={`motion-rise ${styles.answerCard}`}>
        <span className="visually-hidden">Answer: </span>
        <div className={styles.notFound}>
          <span className={styles.notFoundIcon}>
            <Icon name="file" size={20} />
          </span>
          <p className={styles.plain}>{UNCHECKED_TEXT}</p>
        </div>
        {understood}
        {documentRows("Where it appears")}
        {feedback}
      </div>
    );
  }

  const cards = citationMap(answer);
  const terms = matchTerms(question, reading.searchingFor, answer.normalized_question);
  const sources = rankedSources(answer.citations, pin);
  // The source put on top is the first, when it is one of them (rankedSources).
  const pinnedFirst = sources[0] && isPinned(pin, sources[0].document_id, sources[0].page_no, sources[0].ref_id);
  const related = rankedRelated(answer.related_documents ?? [], pin);
  // A row just put on top is drawn again, so it rises into place (motion-rise: transform
  // and opacity only; none with reduced motion).
  const rowKey = (base: string, top: boolean) => (top ? `${base}-top` : base);

  return (
    <div className={`motion-rise ${styles.answerCard}`}>
      <span className="visually-hidden">Answer: </span>
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
      {sources.length > 0 || related.length > 0 || notePages || mentions ? (
        <SourcesBar count={sourcesCount(sources.length, related.length, pageCount, mentions?.total ?? 0)}>
          {sources.length > 0 ? (
            <ul className={styles.sources} aria-label="Sources, most relevant first">
              {sources.map((citation, index) => {
                const top = index === 0 && Boolean(pinnedFirst);
                const target = { document_id: citation.document_id, page_no: citation.page_no, ref_id: citation.ref_id };
                return (
                  <li key={rowKey(String(citation.n), top)} className={top ? "motion-rise" : undefined}>
                    <SourceCard
                      citation={citation}
                      messageId={message.id}
                      hot={hot === citation.n}
                      terms={terms}
                      pinned={top}
                      onPin={() => putOnTop(target, index + 1)}
                    />
                  </li>
                );
              })}
            </ul>
          ) : null}
          {related.length > 0 ? (
            <div>
              <h3 className={styles.subheading}>Also in</h3>
              <ul className={styles.sources} aria-label="Also in">
                {related.map((doc, index) => {
                  const top = isPinned(pin, doc.document_id, doc.page_no);
                  const target = { document_id: doc.document_id, page_no: doc.page_no };
                  return (
                    <li key={rowKey(doc.document_id, top)} className={top ? "motion-rise" : undefined}>
                      <RelatedRow
                        doc={doc}
                        terms={terms}
                        pinned={top}
                        onPin={() => putOnTop(target, sources.length + index + 1)}
                      />
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
          {mentioned}
          {pinFailed ? (
            <p className={styles.sourceMeta} role="status">
              Not saved. Check your connection, then try again.
            </p>
          ) : null}
          {notePages}
        </SourcesBar>
      ) : null}
      {feedback}
    </div>
  );
});
