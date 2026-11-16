// The Grid view of the folder screen (File management F5): one card per document with its
// name, label chip, version ("v1 · newer?" while one waits), type, owner and last change.
// Every name is React text. Actions stay with the List view and the document page.
// Issue 452 (owner, 29 Oct 2026): every card has the same three bands, so a row of cards lines
// up: the name in two lines' room (cut with "…", whole in its title), one line of chips, and
// "Owner · date" at the foot. The whole card opens the document; its words say "Owner" and
// "Last updated" to a screen reader. The label chip, the type and the owner sit over the card's
// link, so their titles (the chip's description, a cut type's or owner's whole words) show.

import Link from "next/link";
import { SHARED } from "@/components/access/words";
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
            <Link href={`/documents/${document.id}`} className={`quiet-link ${styles.cardName}`} title={document.display_name}>
              {document.display_name}
            </Link>
            <span className={styles.cardFacts}>
              {document.sensitivity && <LabelChip label={document.sensitivity} />}
              <span className="tabular">
                {versionCell(document.version_no, newer)}
                {newer && <span className="visually-hidden">{`, ${NEWER_SPOKEN}`}</span>}
              </span>
              {/* PRD 7 (#604): a file in a folder this person can't see. */}
              {document.shared_with_you && <span>{SHARED.row}</span>}
              <span title={docTypeLabel(document.doc_type)}>{docTypeLabel(document.doc_type)}</span>
            </span>
            <span className={styles.cardMeta}>
              {document.owner?.name && (
                <>
                  <span className={styles.cardOwner} title={document.owner.name}>
                    <span className="visually-hidden">{`${FILES_TEXT.columns.owner}: `}</span>
                    {document.owner.name}
                  </span>
                  <span aria-hidden="true">·</span>
                </>
              )}
              <span className={styles.cardDate}>
                <span className="visually-hidden">{`${FILES_TEXT.columns.updatedAt} `}</span>
                {formatDate(when)}
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}
