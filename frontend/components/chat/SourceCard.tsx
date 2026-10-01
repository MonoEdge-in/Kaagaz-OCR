"use client";

// One source behind an answer: its number (matching the chip in the answer),
// the document and page, its label, a zoomed-in preview of the spot (loaded
// only when the card comes near view), the cited text (plain text, it came
// from the document), and a link that opens the page with the spot highlighted.
// A source from an older version says so; one whose document is in Trash or
// deleted shows only "From a deleted document" and its label (citationSource).

import Link from "next/link";
import { useRef } from "react";
import { Icon } from "@/components/ui/Icon";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { PageViewer } from "@/components/viewer/PageViewer";
import type { Citation } from "@/lib/types";
import {
  DELETED_SOURCE_TEXT,
  OLDER_VERSION_TEXT,
  cardId,
  citationLink,
  citationRegion,
  citationSource,
} from "./answer";
import { useNearView } from "./hooks";
import { sourceAction, sourceMeta } from "./logic";
import styles from "./Chat.module.css";

function PreviewSkeleton() {
  return <span className={`skeleton ${styles.previewSkeleton}`} aria-hidden="true" />;
}

// The citation carries its page, so the preview never fetches the whole document.
function SourcePreview({ citation }: { citation: Citation }) {
  const region = citationRegion(citation);
  if (!citation.page || !region) {
    return <p className={styles.previewNote}>The preview is not available. Open the page to see the spot.</p>;
  }
  const cited = {
    id: citation.document_id,
    display_name: citation.document_name,
    detected_type: citation.detected_type,
  };
  return <PageViewer document={cited} page={citation.page} highlights={citation.regions} crop={region} />;
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
      </div>
    </article>
  );
}
