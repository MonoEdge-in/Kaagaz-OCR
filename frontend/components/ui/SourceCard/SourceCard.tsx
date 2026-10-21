// Where an answer's figure is on the page (Components.html, 14): the crop of
// the spot on paper, then the citation number, the document's name and
// version, where it sits (page, folder), and the actions (Open page 2, Copy).
// The crop is the page's own image or its text with the cited Mark; the card
// never shows a value that has no spot.

import type { ReactNode } from "react";
import { CitationChip } from "../Citation/Citation";
import styles from "./SourceCard.module.css";

export interface SourceCardProps {
  n: number;
  /** The crop: an <img> of the spot, or the page's text with a Mark around the cited words. */
  crop: ReactNode;
  name: string;
  /** A VersionChip, once citations carry the version (NB-19). */
  version?: ReactNode;
  /** "Page 2 · clause 3 · Land & Legal / Agreements". */
  meta: ReactNode;
  actions?: ReactNode;
}

export function SourceCard({ n, crop, name, version, meta, actions }: SourceCardProps) {
  return (
    <article className={styles.source} aria-label={`Source ${n}: ${name}`}>
      <div className={styles.crop}>
        <div className={styles.paper}>{crop}</div>
      </div>
      <div className={styles.body}>
        <span className={styles.title}>
          <CitationChip n={n} />
          <strong className={styles.name}>{name}</strong>
          {version}
        </span>
        <span className="k-small k-muted">{meta}</span>
        {actions}
      </div>
    </article>
  );
}
