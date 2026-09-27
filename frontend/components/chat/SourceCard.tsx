"use client";

// One source behind an answer: the document, the page, the cited text (plain
// text, it came from the document), its label, a link that opens the page with
// the spot highlighted, and a small zoomed-in preview of that spot.

import Link from "next/link";
import { useDocumentDetail } from "@/components/viewer/documentCache";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { PageViewer } from "@/components/viewer/PageViewer";
import type { Citation } from "@/lib/types";
import { cardId, citationLink, citationRegion } from "./answer";
import styles from "./Chat.module.css";

function SourcePreview({ citation }: { citation: Citation }) {
  const state = useDocumentDetail(citation.document_id);
  if (state.status === "loading") return <p className="muted">Loading the preview…</p>;
  if (state.status === "error") return <p className="muted">The preview is not available.</p>;
  const page = state.document.pages.find((p) => p.page_no === citation.page_no);
  const region = citationRegion(citation);
  if (!page || !region) return <p className="muted">The preview is not available.</p>;
  return (
    <PageViewer
      document={state.document}
      page={page}
      highlights={citation.regions}
      crop={region}
    />
  );
}

export function SourceCard({ citation, messageId }: { citation: Citation; messageId: string }) {
  const headingId = `${cardId(messageId, citation.n)}-title`;
  return (
    <article
      id={cardId(messageId, citation.n)}
      className={`card stack ${styles.source}`}
      tabIndex={-1}
      aria-labelledby={headingId}
    >
      <div className="row">
        <span className={styles.sourceNumber} aria-hidden="true">
          {citation.n}
        </span>
        <h3 id={headingId} className={styles.sourceTitle}>
          <span className="visually-hidden">{`Source ${citation.n}: `}</span>
          {citation.document_name}
        </h3>
        <span className="muted">Page {citation.page_no}</span>
        <LabelBadge label={citation.label} />
      </div>
      <blockquote className={styles.snippet}>{citation.snippet}</blockquote>
      <SourcePreview citation={citation} />
      <p>
        <Link href={citationLink(citation)}>
          Open page
          <span className="visually-hidden">{` ${citation.page_no} of ${citation.document_name}`}</span>
        </Link>
      </p>
    </article>
  );
}
