"use client";

// One source behind an answer: its number (matching the chip in the answer),
// the document and page, its label, a zoomed-in preview of the spot (loaded
// only when the card comes near view), the cited text (plain text, it came
// from the document), and a link that opens the page with the spot highlighted,
// with "See scanned text" beside it (the page's Scanned text open).
// A source over several pages says "Pages 15 to 16" and gets a chip for each
// later page's spot; a value whose label is on another page says so and gets
// a chip for the label (PRD 3 H4).
// A source from an older version says so; one whose document is in Trash or
// deleted shows only "From a deleted document" and its label (citationSource).
// Cited text with an unsure or illegible word says "Part of this text was hard
// to read." (PRD 5 F6, U7).

import Link from "next/link";
import { useRef } from "react";
import { Icon } from "@/components/ui/Icon";
import { useDocumentDetail } from "@/components/viewer/documentCache";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { PageViewer } from "@/components/viewer/PageViewer";
import type { Citation } from "@/lib/types";
import {
  DELETED_SOURCE_TEXT,
  HARD_TO_READ_TEXT,
  OLDER_VERSION_TEXT,
  cardId,
  citationHighlights,
  citationLink,
  citationRegion,
  citationSource,
  scannedLink,
} from "./answer";
import { useNearView } from "./hooks";
import { labelPlace, pageJumps, sourceAction, sourceMeta } from "./logic";
import styles from "./Chat.module.css";

function PreviewSkeleton() {
  return <span className={`skeleton ${styles.previewSkeleton}`} aria-hidden="true" />;
}

function SourcePreview({ citation }: { citation: Citation }) {
  const state = useDocumentDetail(citation.document_id);
  if (state.status === "loading") return <PreviewSkeleton />;
  const page =
    state.status === "ready"
      ? state.document.pages.find((p) => p.page_no === citation.page_no)
      : undefined;
  const region = citationRegion(citation);
  if (state.status === "error" || !page || !region) {
    return <p className={styles.previewNote}>The preview is not available. Open the page to see the spot.</p>;
  }
  return (
    <PageViewer
      document={state.document}
      page={page}
      highlights={citationHighlights(citation)}
      crop={region}
    />
  );
}

function LazySourcePreview({ citation }: { citation: Citation }) {
  const holder = useRef<HTMLDivElement>(null);
  const near = useNearView(holder);
  return (
    <div ref={holder} className={styles.preview}>
      {near ? <SourcePreview citation={citation} /> : <PreviewSkeleton />}
    </div>
  );
}

export function SourceCard({ citation, messageId }: { citation: Citation; messageId: string }) {
  const id = cardId(messageId, citation.n);
  const headingId = `${id}-title`;
  const source = citationSource(citation);
  if (source === "gone") {
    return (
      <article id={id} className={styles.source} tabIndex={-1} aria-labelledby={headingId}>
        <div className={styles.sourceHeader}>
          <span className={styles.numberMark} aria-hidden="true">
            {citation.n}
          </span>
          <div className={styles.sourceHeading}>
            <h4 id={headingId} className={styles.sourceTitle}>
              <span className="visually-hidden">{`Source ${citation.n}: `}</span>
              {DELETED_SOURCE_TEXT}
            </h4>
            <LabelBadge label={citation.label} />
          </div>
        </div>
      </article>
    );
  }
  const action = sourceAction(citation);
  const labelText = labelPlace(citation);
  const jumps = pageJumps(citation);
  const scanned = scannedLink(citation);
  return (
    <article id={id} className={styles.source} tabIndex={-1} aria-labelledby={headingId}>
      <div className={styles.sourceHeader}>
        <span className={styles.numberMark} aria-hidden="true">
          {citation.n}
        </span>
        <div className={styles.sourceHeading}>
          <h4 id={headingId} className={styles.sourceTitle}>
            <span className="visually-hidden">{`Source ${citation.n}: `}</span>
            {citation.document_name}
          </h4>
          <p className={styles.sourceMeta}>{sourceMeta(citation)}</p>
          {labelText && <p className={styles.sourceMeta}>{labelText}</p>}
          {source === "older" && <p className={styles.sourceMeta}>{OLDER_VERSION_TEXT}</p>}
          {citation.label === "needs_review" ? (
            <Link
              href={`/review?document_id=${encodeURIComponent(citation.document_id)}`}
              className={styles.reviewLink}
              aria-label={`Review values from ${citation.document_name}`}
            >
              <LabelBadge label={citation.label} />
            </Link>
          ) : (
            <LabelBadge label={citation.label} />
          )}
        </div>
      </div>
      <LazySourcePreview citation={citation} />
      {citation.snippet !== null && (
        <blockquote className={styles.snippet}>{citation.snippet}</blockquote>
      )}
      {citation.hard_to_read && (
        <p className={styles.sourceMeta}>
          <Icon name="eye-alert" size={16} /> {HARD_TO_READ_TEXT}
        </p>
      )}
      <div className={styles.sourceAction}>
        {action.reason ? <p className={styles.sourceReason}>{action.reason}</p> : null}
        <Link
          href={citationLink(citation)}
          className={action.check ? `btn ${styles.checkLink}` : styles.openLink}
        >
          <Icon name="open" size={16} />
          {action.text}
          <span className="visually-hidden">{` of ${citation.document_name}`}</span>
        </Link>
        {scanned && (
          <Link href={scanned} className={styles.openLink}>
            <Icon name="scan-text" size={16} />
            See scanned text
            <span className="visually-hidden">{` of ${citation.document_name}`}</span>
          </Link>
        )}
        {jumps.length > 0 && (
          <ul className={styles.docChips} aria-label={`More spots in ${citation.document_name}`}>
            {jumps.map((jump) => (
              <li key={jump.text}>
                <Link className={styles.docChip} href={jump.href}>
                  <Icon name="open" size={16} />
                  {jump.text}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </article>
  );
}
