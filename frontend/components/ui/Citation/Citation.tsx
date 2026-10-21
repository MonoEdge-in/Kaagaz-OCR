// A citation number in an answer (Components.html, 14): a mono number on
// accent-soft. With `href` it opens the page at the spot; with `onSelect` it
// picks its source card; with neither it only labels one (on the card itself).
// The active one is filled, and the same box is outlined on the page.

import Link from "next/link";
import { cx } from "../cx";
import styles from "./Citation.module.css";

export interface CitationChipProps {
  n: number;
  active?: boolean;
  href?: string;
  onSelect?(): void;
}

export function CitationChip({ n, active, href, onSelect }: CitationChipProps) {
  const className = cx(styles.cite, active && styles.active);
  const label = `Source ${n}`;
  if (href) {
    return (
      <Link href={href} className={className} aria-label={label} aria-current={active ? "true" : undefined}>
        {n}
      </Link>
    );
  }
  if (onSelect) {
    return (
      <button type="button" className={className} aria-label={label} aria-pressed={active ?? false} onClick={onSelect}>
        {n}
      </button>
    );
  }
  return (
    <span className={className} aria-label={label} role="img">
      {n}
    </span>
  );
}
