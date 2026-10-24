"use client";

// The sources of an answer, compact (Tushar, 16 Oct 2026): no page image and no boxes
// in Ask, only where the source is. A cited source (SourceCard) is one row: its number
// (matching the chip in the answer), the file icon, the document's name on one line,
// "Page 3 · PDF", up to four lines of the cited text around the question's words, those
// words marked (a tint and bold, never colour alone), "Open page" (the page opens at the
// spot) and "Most relevant", which puts it on top of the list (aria-pressed once it is).
// A document the search found but the answer does not cite (RelatedRow, "Also in")
// is the same row with two lines and no number. Not found's closest documents are the
// same row with only the name (DocumentRow).
// A source over several pages says "Pages 15 to 16" and gets a chip for each later
// page's spot; a value whose label is on another page says so and gets a chip for the
// label (PRD 3 H4). A source from an older version says so; one whose document is in
// Trash or deleted shows only "From a deleted document" (citationSource). No label
// badge: no Needs review in Ask; that is the Review screen's. Cited text with an unsure
// or illegible word says "Part of this text was hard to read." (PRD 5 F6, U7). Its chip
// pointed at or focused lights the card (data-hot). All text is plain text.

import Link from "next/link";
import { memo } from "react";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { pageLink } from "@/lib/regions";
import type { Citation, Mentions, RelatedDocument } from "@/lib/types";
import {
  DELETED_SOURCE_TEXT,
  HARD_TO_READ_TEXT,
  OLDER_VERSION_TEXT,
  cardId,
  citationLink,
  citationSource,
  documentLink,
  linkWords,
  markParts,
  matchTerms,
  mentionPages,
  snippetAround,
} from "./answer";
import { labelPlace, openText, pageJumps, sourceMeta } from "./logic";
import styles from "./Chat.module.css";

export const PIN_LABEL = "Most relevant";

/** The text with the question's words marked; plain text only. */
function Marked({ text, terms, short = false }: { text: string; terms: readonly string[]; short?: boolean }) {
  return (
    <p className={short ? `${styles.snippet} ${styles.snippetShort}` : styles.snippet}>
      {markParts(snippetAround(text, terms), terms).map((part, index) =>
        part.mark ? <mark key={index}>{part.text}</mark> : part.text,
      )}
    </p>
  );
}

/** "Most relevant": puts this source on top; pressed once it is. */
function PinButton({ name, pinned, onPin }: { name: string; pinned: boolean; onPin?: () => void }) {
  if (!onPin) return null;
  return (
    <IconButton
      icon="to-top"
      label={pinned ? `${PIN_LABEL}: ${name}` : `${PIN_LABEL}: put ${name} on top`}
      title={PIN_LABEL}
      className={`btn-quiet ${styles.pin}`}
      aria-pressed={pinned}
      onClick={pinned ? undefined : onPin}
    />
  );
}

function Title({ id, prefix, name }: { id: string; prefix: string; name: string }) {
  return (
    <h4 id={id} className={styles.sourceTitle} title={name}>
      <Icon name="file" size={16} />
      <span className="visually-hidden">{prefix}</span>
      <span className={styles.sourceName}>{name}</span>
    </h4>
  );
}

// Memo: a chip pointed at or focused re-renders only the two cards whose `hot` changed.
export const SourceCard = memo(function SourceCard({
  citation,
  messageId,
  hot = false,
  terms = [],
  pinned = false,
  onPin,
}: {
  citation: Citation;
  messageId: string;
  /** Its chip in the answer is pointed at or focused: the card lights up. */
  hot?: boolean;
  /** The question's words, marked in the text (matchTerms). */
  terms?: readonly string[];
  /** It is the source put on top of this answer. */
  pinned?: boolean;
  onPin?: () => void;
}) {
  const id = cardId(messageId, citation.n);
  const headingId = `${id}-title`;
  const prefix = `Source ${citation.n}: `;
  const number = (
    <span className={styles.numberMark} aria-hidden="true">
      {citation.n}
    </span>
  );
  if (citationSource(citation) === "gone") {
    return (
      <article id={id} className={styles.source} tabIndex={-1} aria-labelledby={headingId} data-hot={hot || undefined}>
        {number}
        <div className={styles.sourceBody}>
          <h4 id={headingId} className={styles.sourceTitle}>
            <span className="visually-hidden">{prefix}</span>
            {DELETED_SOURCE_TEXT}
          </h4>
        </div>
      </article>
    );
  }
  const labelText = labelPlace(citation);
  const jumps = pageJumps(citation, terms);
  return (
    <article id={id} className={styles.source} tabIndex={-1} aria-labelledby={headingId} data-hot={hot || undefined}>
      {number}
      <div className={styles.sourceBody}>
        <Title id={headingId} prefix={prefix} name={citation.document_name} />
        <p className={styles.sourceMeta}>
          {sourceMeta(citation)}
          {labelText ? ` · ${labelText}` : null}
          {citationSource(citation) === "older" ? ` · ${OLDER_VERSION_TEXT}` : null}
        </p>
        {citation.snippet !== null && <Marked text={citation.snippet} terms={terms} />}
        {citation.hard_to_read && (
          <p className={styles.sourceMeta}>
            <Icon name="eye-alert" size={16} /> {HARD_TO_READ_TEXT}
          </p>
        )}
        <div className={styles.sourceAction}>
          <Link href={citationLink(citation, terms)} className={styles.openLink}>
            <Icon name="open" size={16} />
            {openText(citation)}
            <span className="visually-hidden">{` of ${citation.document_name}`}</span>
          </Link>
          {jumps.map((jump) => (
            <Link key={jump.text} className={styles.openLink} href={jump.href}>
              {jump.text}
              <span className="visually-hidden">{` of ${citation.document_name}`}</span>
            </Link>
          ))}
        </div>
      </div>
      <PinButton name={citation.document_name} pinned={pinned} onPin={onPin} />
    </article>
  );
});

/** "Also in": a document the search found that the answer does not cite. */
export function RelatedRow({
  doc,
  terms,
  pinned = false,
  onPin,
}: {
  doc: RelatedDocument;
  terms: readonly string[];
  pinned?: boolean;
  onPin?: () => void;
}) {
  const href = pageLink(doc.document_id, doc.page_no, doc.regions[0], { words: linkWords(...terms) });
  return (
    <article className={styles.source}>
      <div className={styles.sourceBody}>
        <Title id={`related-${doc.document_id}`} prefix="Also in: " name={doc.document_name} />
        <p className={styles.sourceMeta}>{sourceMeta(doc)}</p>
        <Marked text={doc.snippet} terms={terms} short />
        <div className={styles.sourceAction}>
          <Link href={href} className={styles.openLink}>
            <Icon name="open" size={16} />
            {openText(doc)}
            <span className="visually-hidden">{` of ${doc.document_name}`}</span>
          </Link>
        </div>
      </div>
      <PinButton name={doc.document_name} pinned={pinned} onPin={onPin} />
    </article>
  );
}

/**
 * "Also mentions “Jalna”" (Tushar, 17 Oct 2026): every other document holding the
 * question's key terms, as the same compact rows as "Also in": the name, its pages and two
 * lines with the terms marked, opening at the page with them lit (package H, linkWords).
 * "and N more" past the list.
 */
export function MentionsSection({ mentions }: { mentions: Mentions }) {
  const terms = matchTerms(...mentions.terms);
  const words = linkWords(...mentions.terms);
  const heading = `Also mentions ${mentions.terms.map((term) => `“${term}”`).join(" · ")}`;
  const more = mentions.total - mentions.documents.length;
  return (
    <div>
      <h3 className={styles.subheading}>
        {heading} <span className={styles.sourceMeta}>· {mentions.total}</span>
      </h3>
      <ul className={styles.sources} aria-label={heading}>
        {mentions.documents.map((doc) => (
          <li key={doc.document_id}>
            <article className={styles.source}>
              <div className={styles.sourceBody}>
                <Title id={`mention-${doc.document_id}`} prefix="Also mentions: " name={doc.document_name} />
                <p className={styles.sourceMeta}>{mentionPages(doc.pages, doc.page_no)}</p>
                <Marked text={doc.snippet} terms={terms} short />
                <div className={styles.sourceAction}>
                  <Link href={pageLink(doc.document_id, doc.page_no, undefined, { words })} className={styles.openLink}>
                    <Icon name="open" size={16} />
                    Open page {doc.page_no}
                    <span className="visually-hidden">{` of ${doc.document_name}`}</span>
                  </Link>
                </div>
              </div>
            </article>
          </li>
        ))}
      </ul>
      {more > 0 ? <p className={styles.sourceMeta}>and {more} more</p> : null}
    </div>
  );
}

/** Not found's closest documents and an unchecked answer's: the same row, the name only; it
 * opens with the question's words lit (package H). */
export function DocumentRow({ id, name, terms = [] }: { id: string; name: string; terms?: readonly string[] }) {
  return (
    <Link className={`${styles.source} ${styles.documentRow}`} href={documentLink(id, terms)} title={name}>
      <Icon name="file" size={16} />
      <span className={styles.sourceName}>{name}</span>
    </Link>
  );
}
