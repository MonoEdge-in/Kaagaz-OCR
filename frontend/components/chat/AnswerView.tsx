"use client";

// One answer card. In order: what a follow-up was searched for ("Searching for: …",
// PRD 3 H1) and how the question was understood (only when that differs), the
// sentences with citation chips (each opens its document at the cited spot, lit), the source cards,
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
// PRD 6 A (24 Oct 2026), an answer in contract v2: its opening first, then the short answer,
// then the documents it lists (DocumentList: title, why, each figure as printed with its label
// beside it, the page to open; at most 10, then how many more), then what was assumed. A
// not-found says decision 9's soft line (naming the words no document has, #410) and lists its
// closest documents, the first 3 open and the rest in a closed disclosure (#413). Labels come back in
// Ask (decision 1): beside a figure or a total, never on a document's title. Answers saved
// before v2 render as they did. The card is a focus target: a new answer gets the focus.

import Link from "next/link";
import { memo, useState } from "react";
import type React from "react";
import { Icon } from "@/components/ui/Icon";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { understoodNotes } from "@/components/words/words";
import { pinSource } from "@/lib/api";
import { useBackFrom, withFrom } from "@/lib/backLink";
import type { Answer, Message, SourcePin } from "@/lib/types";
import {
  answerCardId,
  citationLink,
  citationMap,
  documentLink,
  figureOf,
  isPinned,
  listedDocuments,
  matchTerms,
  mentionsOf,
  notFoundText,
  questionReading,
  scriptParts,
  rankedRelated,
  rankedSources,
  readingNote,
  sentenceCitations,
  sourcesCount,
  UNCHECKED_TEXT,
} from "./answer";
import { FeedbackControls } from "./FeedbackControls";
import { DocumentRow, MentionsSection, RelatedRow, SourceCard } from "./SourceCard";
import styles from "./Chat.module.css";

/**
 * One collapsible bar holding an answer's sources (Tushar, 16 Oct 2026): closed at
 * first, its summary saying what is inside ("Sources · 4 · Also in 3 documents",
 * "Sources · 20 pages to check · 3 documents").
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

/** Text with each Devanagari run marked lang="mr" (PRD 6 A layout rules). Plain text only. */
function Script({ text }: { text: string }) {
  return (
    <>
      {scriptParts(text).map((part, index) =>
        part.lang ? (
          <span key={index} lang={part.lang}>
            {part.text}
          </span>
        ) : (
          part.text
        ),
      )}
    </>
  );
}

/** PRD 6 A: the documents a v2 answer lists, each with its title and why, the figures its
 * sources print with their labels beside them, and the pages to open (a closest document,
 * citing nothing, opens at its start); then how many more are in Sources. #413: with `shown`,
 * only the first that many are listed, the rest in a closed "N more documents" disclosure. */
function DocumentList({ answer, terms, shown }: { answer: Answer; terms: readonly string[]; shown?: number }) {
  const from = useBackFrom();
  const { documents, more } = listedDocuments(answer);
  if (documents.length === 0) return null;
  const item = (doc: (typeof documents)[number]) => {
    const pages = doc.sources.filter((c, i, all) => all.findIndex((o) => o.page_no === c.page_no) === i);
    const figures = doc.sources.flatMap((c) => {
      const figure = figureOf(c);
      return figure ? [{ c, figure }] : [];
    });
    // Vrushit UAT 3 (27 Oct 2026): one compact line, the title opening its first cited spot,
    // lit, then each other page as a small link; no row of page chips.
    const opens = withFrom(pages[0] ? citationLink(pages[0], terms) : documentLink(doc.document_id, terms), from);
    return (
      <li key={doc.document_id} className={styles.docItem}>
        <p className={styles.docTitle}>
          <Link className={styles.docLink} href={opens}>
            <Script text={doc.title} />
          </Link>
          {pages.length > 0 ? (
            <span className={styles.docPages}>
              {pages.map((c) => (
                <Link key={c.n} href={withFrom(citationLink(c, terms), from)}>
                  p. {c.page_no}
                  <span className="visually-hidden">, open the highlighted spot</span>
                </Link>
              ))}
            </span>
          ) : null}
        </p>
        {doc.why ? (
          <p className={styles.docWhy}>
            <Script text={doc.why} />
          </p>
        ) : null}
        {figures.length > 0 ? (
          <ul className={styles.docFigures} aria-label="Figures as printed">
            {figures.map(({ c, figure }) => (
              <li key={c.n}>
                <span className={styles.figure}>
                  <Script text={figure} />
                </span>{" "}
                {/* A figure with an unsure or illegible word is never shown as Verified. */}
                <LabelBadge label={c.hard_to_read ? "needs_review" : c.label} />
              </li>
            ))}
          </ul>
        ) : null}
      </li>
    );
  };
  const rest = shown === undefined ? [] : documents.slice(shown);
  return (
    <div>
      <ul className={styles.docList} aria-label="Documents">
        {documents.slice(0, documents.length - rest.length).map(item)}
      </ul>
      {rest.length > 0 ? (
        <details className={styles.moreDocs}>
          <summary className={styles.sourcesSummary}>
            <Icon name="chevron-right" size={16} className={styles.sourcesChevron} />
            {counted(rest.length, "more document", "more documents")}
          </summary>
          <ul className={styles.docList} aria-label="More documents">
            {rest.map(item)}
          </ul>
        </details>
      ) : null}
      {more > 0 ? (
        <p className={styles.understood}>{counted(more, "more document is", "more documents are")} in Sources.</p>
      ) : null}
    </div>
  );
}

// Memo: the chat keeps each message object as it was when a new answer arrives,
// so old answer cards skip re-rendering (UI quality council, 15 Oct 2026).
/** Over an answer's one-press questions (24 Oct 2026). */
export const TRY_THESE = "Try one of these";
/** #413 (Vrushit's UAT): a not-found lists its first 3 closest documents; the rest wait, closed. */
const CLOSEST_SHOWN = 3;
/** KZ-006 (24 Oct 2026): every model was down; as routers/chat.py OUTAGE_LINE. */
export const OUTAGE_TEXT = "The AI is not responding right now, so this is not a written answer. Ask again in a minute."
/** A search answer the AI did not write (off on this server, or not answering): the lines that
 * matched, quoted. It read as a written answer before (QA pass, 25 Oct 2026). */
export const QUOTED_TEXT =
  "The AI did not write this answer: these are the lines that best match your question, quoted from your documents. Check that they answer it."

export const AnswerView = memo(function AnswerView({
  message,
  question,
  onAskAll,
  onAskQuestion,
  readOnly = false,
  settle,
}: {
  message: Message;
  question: string | null;
  onAskAll?: () => void;
  /** Ask one of the answer's suggestions (24 Oct 2026); absent while another answer is on its way. */
  onAskQuestion?: (question: string) => void;
  /** Someone else's answer (the admin's Answer quality page, PRD 6 F2): no rating, no Most relevant. */
  readOnly?: boolean;
  /** #535: the answer just streamed (StreamingAnswer.tsx): the card takes the preview's place
   * without rising, and what the preview did not have (the label, the sources) fades in;
   * "changed", its sentences too, as they differ from the preview. */
  settle?: "same" | "changed";
}) {
  // The source a chip is pointed at or focused: its card lights up (UI quality council, 15 Oct 2026).
  const [hot, setHot] = useState<number | null>(null);
  // The source put on top: the saved pick, then this person's, at once (the server keeps it).
  const [pin, setPin] = useState<SourcePin | null>(message.pin ?? null);
  const [pinFailed, setPinFailed] = useState(false);
  const from = useBackFrom(); // the conversation, for the documents' back link (#436)
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

  const card = { id: answerCardId(message.id), tabIndex: -1 };
  const motion = settle ? `${styles.settled}${settle === "changed" ? ` ${styles.settledChanged}` : ""}` : "motion-rise";
  if (!answer) {
    return (
      <div {...card} className={`${motion} ${styles.answerCard}`}>
        <span className="visually-hidden">Answer: </span>
        <p className={styles.plain}>{message.text}</p>
      </div>
    );
  }

  const reading = questionReading(question, answer);
  // The question's words, marked in the sources and carried by every link into the viewer (package H).
  const terms = matchTerms(question, reading.searchingFor, answer.normalized_question);
  const v2 = answer.version === 2;
  const opening =
    v2 && answer.opening ? (
      <p className={styles.opening}>
        <Script text={answer.opening} />
      </p>
    ) : null;
  const listed = v2 ? <DocumentList answer={answer} terms={terms} /> : null;
  const assumed =
    v2 && answer.assumptions && answer.assumptions.length > 0 ? (
      <p className={styles.understood}>{answer.assumptions.join(" ")}</p>
    ) : null;
  // v2 lists its closest documents in DocumentList; older answers in the Sources bar.
  const closestInBar = v2 ? [] : answer.closest_documents;
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
              <Link className={styles.docChip} href={withFrom(page.href, from)}>
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
  const feedback = readOnly ? null : (
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
              <DocumentRow id={doc.id} name={doc.display_name} terms={terms} />
            </li>
          ))}
        </ul>
      </div>
    ) : null;

  // Questions to ask with one press (24 Oct 2026): after small talk, or when nothing was close.
  const suggested = answer.suggestions ?? [];
  const tryThese =
    suggested.length > 0 && !readOnly ? (
      <div className={styles.tryThese}>
        <h3 className={styles.subheading}>{TRY_THESE}</h3>
        <ul className={styles.tryList}>
          {suggested.map((text) => (
            <li key={text}>
              <button type="button" className="k-suggest" disabled={!onAskQuestion} onClick={() => onAskQuestion?.(text)}>
                <Script text={text} />
              </button>
            </li>
          ))}
        </ul>
      </div>
    ) : null;

  // "hi", "thanks", "what can you do" (24 Oct 2026): a line, then any list, then questions to try.
  if (answer.route === "small_talk") {
    const [line, ...list] = answer.sentences;
    return (
      <div {...card} className={`${motion} ${styles.answerCard}`}>
        <span className="visually-hidden">Answer: </span>
        <p className={styles.answerText}>{line ? <Script text={line.text} /> : null}</p>
        {list.length > 0 ? (
          <ul className={styles.canDo}>
            {list.map((sentence) => (
              <li key={sentence.text}>{sentence.text}</li>
            ))}
          </ul>
        ) : null}
        {tryThese}
      </div>
    );
  }

  if (answer.status === "not_found") {
    const closest = v2 ? (answer.documents ?? []).length : answer.closest_documents.length;
    return (
      <div {...card} className={`${motion} ${styles.answerCard}`}>
        <span className="visually-hidden">Answer: </span>
        {opening}
        <div className={styles.notFound}>
          <span className={styles.notFoundIcon}>
            <Icon name={answer.outage ? "alert" : "search-off"} size={20} />
          </span>
          <p className={styles.plain}>
            {answer.outage ? OUTAGE_TEXT : notFoundText(closest, answer.missing_words ?? [])}
          </p>
        </div>
        {v2 ? <DocumentList answer={answer} terms={terms} shown={CLOSEST_SHOWN} /> : null}
        {noteText}
        {understood}
        {notePages || closestInBar.length > 0 || mentions ? (
          <SourcesBar
            count={[
              pageCount > 0 ? counted(pageCount, "page to check", "pages to check") : "",
              closestInBar.length > 0 ? counted(closestInBar.length, "document", "documents") : "",
              mentions ? `Mentioned in ${counted(mentions.total, "document", "documents")}` : "",
            ]
              .filter(Boolean)
              .join(" · ")}
          >
            {notePages}
            {v2 ? null : documentRows("Closest documents")}
            {mentioned}
          </SourcesBar>
        ) : null}
        {tryThese}
        <div hidden={readOnly}>
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
      <div {...card} className={`${motion} ${styles.answerCard}`}>
        <span className="visually-hidden">Answer: </span>
        {opening}
        <div className={styles.notFound}>
          <span className={styles.notFoundIcon}>
            <Icon name="file" size={20} />
          </span>
          <p className={styles.plain}>{UNCHECKED_TEXT}</p>
        </div>
        {understood}
        {listed ?? documentRows("Where it appears")}
        {feedback}
      </div>
    );
  }

  const cards = citationMap(answer);
  const sources = rankedSources(answer.citations, pin);
  // The source put on top is the first, when it is one of them (rankedSources).
  const pinnedFirst = sources[0] && isPinned(pin, sources[0].document_id, sources[0].page_no, sources[0].ref_id);
  const related = rankedRelated(answer.related_documents ?? [], pin);
  // A row just put on top is drawn again, so it rises into place (motion-rise: transform
  // and opacity only; none with reduced motion).
  const rowKey = (base: string, top: boolean) => (top ? `${base}-top` : base);

  // Decision 1: the answer's label beside its figure or total (a sentence with a digit).
  const labelled = v2 && answer.label && answer.sentences.some((s) => /\d/.test(s.text)) ? answer.label : null;
  return (
    <div {...card} className={`${motion} ${styles.answerCard}`}>
      <span className="visually-hidden">Answer: </span>
      {opening}
      {answer.status === "answered" && answer.route === "search" && !answer.model_used ? <p className={styles.understood}>{QUOTED_TEXT}</p> : null}
      {understood}
      <p className={styles.answerText}>
        {answer.sentences.map((sentence, index) => (
          <span key={index}>
            {index > 0 ? " " : null}
            <Script text={sentence.text} />
            {/* Vrushit UAT 3 (27 Oct 2026): a number opens its document at the cited spot, lit. */}
            {sentenceCitations(sentence, cards).map((n) => {
              const cited = cards.get(n);
              return cited ? (
                <Link
                  key={n}
                  href={withFrom(citationLink(cited, terms), from)}
                  className={`${styles.numberMark} ${styles.cite}`}
                  aria-label={`Source ${n}: ${cited.document_name}, page ${cited.page_no}`}
                  onPointerEnter={() => setHot(n)}
                  onPointerLeave={() => setHot(null)}
                  onFocus={() => setHot(n)}
                  onBlur={() => setHot(null)}
                >
                  {n}
                </Link>
              ) : null;
            })}
          </span>
        ))}
        {labelled ? (
          <>
            {" "}
            <LabelBadge label={labelled} />
          </>
        ) : null}
      </p>
      {listed}
      {assumed}
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
                      onPin={readOnly ? undefined : () => putOnTop(target, index + 1)}
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
                        onPin={readOnly ? undefined : () => putOnTop(target, sources.length + index + 1)}
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
