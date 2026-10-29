"use client";

// The panels beside the page (PRD 3 Y1 to Y3): the related documents; and first, the
// Attention panel (PRD 5 U5): every
// reading problem in the document, by page, each page a jump link, with the
// shown page's marks as a list of the same buttons as on the page. While anything is left to settle
// (toSettle, #443), it carries the Ready to verify dashed edge, "N to check" and a lead line (board 2).
// UI audit DD7 (16 Oct 2026): each mark is a short chip, the word, "1/3" and a jump icon
// (its full name for screen readers); pointing at one lights its mark on the page, and
// pointing at a mark on the page lights its chip (`hot`).
// Every text came from the document or the AI model, so it is shown as plain text.
// Vrushit UAT (25 Oct 2026): the Invoice section and its line cards are gone from the page.

import Link from "next/link";
import type { ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import { labelBadgeClass, labelIcon } from "@/components/viewer/labels";
import { groupLinks, linkText, untilRead } from "@/components/viewer/panels";
import { ALL_CLEAR, attentionPages, toSettle } from "@/components/viewer/pageProblems";
import type { OpenWord } from "@/components/viewer/pageProblems";
import { markChipText, markName, marksOnPage } from "@/components/viewer/readingMarks";
import { withFrom } from "@/lib/backLink";
import { docTypeLabel } from "@/lib/format";
import type { DocumentDetail, DocumentStatus, PageSummary, ReadingMark, WordFlag } from "@/lib/types";
import styles from "./DocumentView.module.css";

/** Vrushit UAT 3 (27 Oct 2026): a closed, quiet disclosure with its count; each document one line,
 * its name and then, muted, its type and the number it shares. */
export function RelatedPanel({ detail }: { detail: DocumentDetail }) {
  const groups = groupLinks(detail.links);
  if (groups.length === 0) {
    return (
      <section className={styles.panel} aria-labelledby="related-heading">
        <h2 id="related-heading" className={styles.relatedHeading}>
          Related documents
        </h2>
        <p className={styles.pageNote}>{untilRead(detail.status, "No other document shares a number with this one.")}</p>
      </section>
    );
  }
  return (
    <details className={`${styles.panel} ${styles.related}`}>
      <summary className={styles.relatedSummary}>
        <Icon name="chevron-right" size={16} className={styles.relatedChevron} />
        <h2 className={styles.relatedHeading}>Related documents</h2>
        <span className={`tabular ${styles.pageNote}`}>{groups.length.toLocaleString("en-IN")}</span>
      </summary>
      <ul className={styles.relatedList}>
        {groups.map(([link, ...more]) => (
          <li key={link.id} className={styles.relatedItem}>
            <Link href={withFrom(`/documents/${link.id}`, `/documents/${encodeURIComponent(detail.id)}`)}>
              {link.display_name}
            </Link>
            <span className={styles.pageNote}>
              {[docTypeLabel(link.doc_type), ...[link, ...more].map((shared) => linkText(shared, detail.values))].join(
                " · ",
              )}
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** PRD 5 U5: the document's reading problems by page, with jump links; the shown page's marks as buttons. */
export function AttentionPanel({
  status,
  pages,
  open,
  current,
  marks,
  onPage,
  onMark,
  onCheck,
  hot = null,
  onHot = () => undefined,
  fix = null,
}: {
  /** The shown page's fix (PageProblems inline), under its reasons: one box (24 Oct 2026). */
  fix?: ReactNode;
  status: DocumentStatus;
  pages: PageSummary[];
  open: OpenWord[] | null; // the words still open on the review list; null until they load (toSettle)
  current: number | null; // the page shown
  marks: ReadingMark[]; // the shown page's marks
  onPage: (pageNo: number) => void;
  onMark: (mark: ReadingMark, name: string) => void;
  onCheck: (pageNo: number) => void; // #443: check a good page's open words, in review
  hot?: number | null; // the mark pointed at, here or on the page (its index on the page)
  onHot?: (index: number | null) => void;
}) {
  const problemPages = attentionPages(pages, open ?? []);
  const shownMarks = current === null ? [] : marksOnPage(marks, current); // as numbered on the page
  const lead = toCheck(pages, open);
  return (
    <section
      className={lead ? `${styles.panel} ${styles.attentionToCheck}` : styles.panel}
      aria-labelledby="attention-heading"
    >
      <h2 id="attention-heading" className={styles.valuesHeading}>
        Attention
        {lead && (
          <span className={`${labelBadgeClass("needs_review")} tabular`}>
            <Icon name={labelIcon("needs_review")} size={16} />
            {lead.count.toLocaleString("en-IN")} to check
          </span>
        )}
      </h2>
      {lead && <p className={styles.attentionLead}>{lead.text}</p>}
      {problemPages.length === 0 ? (
        <p className="muted">{untilRead(status, ALL_CLEAR)}</p>
      ) : (
        <ul className={styles.panelList}>
          {problemPages.map((item) => (
            <li key={item.pageNo}>
              <button
                type="button"
                className={`quiet-link ${styles.valueButton}`}
                aria-current={item.pageNo === current ? "page" : undefined}
                onClick={() => onPage(item.pageNo)}
              >
                Page {item.pageNo}
                <span className="visually-hidden">, show it</span>
              </button>
              {item.kept && <span className={styles.pageNote}> Kept as is</span>}
              <ul className={styles.checkReasons}>
                {item.lines.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              {item.words !== undefined && (
                <div className={styles.problemActions}>
                  <button type="button" className="btn" onClick={() => onCheck(item.pageNo)}>
                    {`Check ${item.words === 1 ? "it" : "them"}`}
                  </button>
                </div>
              )}
              {item.pageNo === current ? fix : null}
              {item.pageNo === current && shownMarks.length > 0 && (
                <ul className={styles.markChips} aria-label={`Marks on page ${item.pageNo}`}>
                  {shownMarks.map((mark, index) => {
                    const name = markName(mark, index, shownMarks.length);
                    return (
                      <li key={index}>
                        <button
                          type="button"
                          className={`btn ${styles.markChip}`}
                          data-quality={mark.quality}
                          data-hot={hot === index ? "true" : undefined}
                          aria-label={name}
                          title={name}
                          onClick={() => onMark(mark, name)}
                          onPointerEnter={() => onHot(index)}
                          onPointerLeave={() => onHot(null)}
                          onFocus={() => onHot(index)}
                          onBlur={() => onHot(null)}
                        >
                          <bdi className={`mono ${styles.markWord}`}>{markChipText(mark)}</bdi>
                          <span className="tabular">
                            {index + 1}/{shownMarks.length}
                          </span>
                          <Icon name="open" size={16} />
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The Needs attention tab's count (DD6): what is left to settle (toSettle), as "N to check" says;
 * `open` null until the words load, or when they cannot: the pages' marked spots then. */
export const attentionCount = (pages: PageSummary[], open: Pick<WordFlag, "page_no">[] | null) =>
  toSettle(pages, open).count;

/**
 * The Attention panel's "N to check" and its lead line, from toSettle (#443, #444): the words still
 * open and the pages still to keep as is; null when there are none.
 */
function toCheck(
  pages: PageSummary[],
  openWords: Pick<WordFlag, "page_no">[] | null,
): { count: number; text: string } | null {
  const { count, pages: pagesWithAny } = toSettle(pages, openWords);
  if (count === 0) return null;
  const one = pagesWithAny.length === 1;
  const where = one ? `page ${pagesWithAny[0]}` : `${pagesWithAny.length.toLocaleString("en-IN")} pages`;
  const them = count === 1 ? "it" : "them";
  const problems = count === 1 ? "1 reading problem" : `${count.toLocaleString("en-IN")} reading problems`;
  return {
    count,
    text: `${problems} on ${where}. Check ${them} on the ${one ? "page" : "pages"} before you rely on ${them}.`,
  };
}
