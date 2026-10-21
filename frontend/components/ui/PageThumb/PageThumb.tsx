// A page in the viewer's strip (Components.html, 15): the page, its number, an
// accent edge on the current one, a "!" when it couldn't be read and a dashed
// "?" when it has values to verify. The button's name says all of it, so the
// marks are never the only signal.

import type { ReactNode } from "react";
import { formatNumber } from "@/lib/format";
import { cx } from "../cx";
import styles from "./PageThumb.module.css";

export type PageFlag = "unreadable" | "review";

export interface PageThumbProps {
  page: number;
  current?: boolean;
  flag?: PageFlag;
  /** With "review": how many values on it to check. */
  toReview?: number;
  /** The page's picture, once drawn; the ruled paper shows until then. */
  image?: ReactNode;
  onSelect(): void;
}

/** "Page 3, could not be read", "Page 4, 2 values to verify", "Page 1, current". */
export function thumbName(page: number, current?: boolean, flag?: PageFlag, toReview?: number): string {
  const parts = [`Page ${formatNumber(page)}`];
  if (current) parts.push("current");
  if (flag === "unreadable") parts.push("could not be read");
  if (flag === "review") {
    parts.push(toReview ? `${formatNumber(toReview)} ${toReview === 1 ? "value" : "values"} to verify` : "values to verify");
  }
  return parts.join(", ");
}

export function PageThumb({ page, current, flag, toReview, image, onSelect }: PageThumbProps) {
  return (
    <button
      type="button"
      className={cx(styles.thumb, current && styles.active)}
      aria-label={thumbName(page, current, flag, toReview)}
      aria-current={current ? "page" : undefined}
      onClick={onSelect}
    >
      <span className={styles.page}>
        {image}
        {flag ? (
          <span className={cx(styles.flag, flag === "review" && styles.flagReview)} aria-hidden="true">
            {flag === "review" ? "?" : "!"}
          </span>
        ) : null}
      </span>
      <span aria-hidden="true">{formatNumber(page)}</span>
    </button>
  );
}

/** The strip of thumbnails beside the page. */
export function PageThumbs({ label, children }: { label: string; children: ReactNode }) {
  return (
    <nav className={styles.thumbs} aria-label={label}>
      {children}
    </nav>
  );
}
