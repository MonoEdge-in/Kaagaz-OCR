// The Grid view of the folder screen (File management F5): one card per document with its
// name, label chip, version ("v1 · newer?" while one waits), type, owner and last change.
// Every name is React text. Actions stay with the List view and the document page.

import Link from "next/link";
import { docTypeLabel, formatDate } from "@/lib/format";
import type { DocumentSummary } from "@/lib/types";
import { LabelChip } from "./LabelChip";
import { FILES_TEXT, NEWER_SPOKEN, versionCell } from "./words";
import styles from "./Files.module.css";

export function DocumentsGrid({ items, label }: { items: readonly DocumentSummary[]; label: string }) {
  return (
    <ul className={styles.grid} aria-label={label}>
      {items.map((document) => {
        const newer = document.pending_suggestion?.role === "candidate";
        const when = document.updated_at ?? document.created_at;
        return (
          <li key={document.id} className={styles.card}>
            <Link href={`/documents/${document.id}`} className={`quiet-link ${styles.cardName}`}>
              {document.display_name}
            </Link>
            <span className={styles.cardFacts}>
              {document.sensitivity && <LabelChip label={document.sensitivity} />}
              <span className="tabular">
                {versionCell(document.version_no, newer)}
                {newer && <span className="visually-hidden">{`, ${NEWER_SPOKEN}`}</span>}
              </span>
              <span>{docTypeLabel(document.doc_type)}</span>
            </span>
            <span className={styles.cardMeta}>
              {document.owner?.name ? `${FILES_TEXT.columns.owner}: ${document.owner.name} · ` : ""}
              {`${FILES_TEXT.columns.updatedAt} ${formatDate(when)}`}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
