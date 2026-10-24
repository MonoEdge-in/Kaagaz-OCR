"use client";

// The panels beside the page (PRD 3 Y1 to Y3): the document card, the related
// documents and the invoice; and first, the Attention panel (PRD 5 U5): every
// reading problem in the document, by page, each page a jump link, with the
// shown page's marks as a list of the same buttons as on the page. While a page not kept as is
// has problems, it carries the Ready to verify dashed edge, "N to check" and a lead line (board 2).
// UI audit DD7 (16 Oct 2026): each mark is a short chip, the word, "1/3" and a jump icon
// (its full name for screen readers); pointing at one lights its mark on the page, and
// pointing at a mark on the page lights its chip (`hot`).
// Each item with a spot is a button that shows it
// on its page, with its label's spot too. Every text came from the document or
// the AI model, so it is shown as plain text; the AI's own words sit under a
// heading that says so.

import Link from "next/link";
import type { ReactNode } from "react";
import table from "@/components/documents/Table.module.css";
import { Icon } from "@/components/ui/Icon";
import { labelBadgeClass, labelIcon } from "@/components/viewer/labels";
import {
  INVOICE_FIELDS,
  LINE_FIELDS,
  MONEY_LINE_FIELDS,
  checkChip,
  groupLinks,
  invoiceSpot,
  linkText,
  untilRead,
} from "@/components/viewer/panels";
import type { Spot } from "@/components/viewer/panels";
import { fieldText } from "@/components/viewer/labels";
import { ALL_CLEAR, attentionPages } from "@/components/viewer/pageProblems";
import type { AttentionPage } from "@/components/viewer/pageProblems";
import { markChipText, markName, marksOnPage } from "@/components/viewer/readingMarks";
import { sameRegion } from "@/components/viewer/selection";
import { WhatWasRead } from "@/components/viewer/WhatWasRead";
import { docTypeLabel } from "@/lib/format";
import type { DocumentDetail, DocumentStatus, ExtractedValue, PageSummary, ReadingMark, Region } from "@/lib/types";
import styles from "./DocumentView.module.css";

interface PanelProps {
  detail: DocumentDetail;
  active: Region | null; // the spot shown now
  onShow: (spot: Spot) => void;
}

/** One value as a button to its spot, or plain text when it has none. */
function SpotButton({
  spot,
  text,
  active,
  onShow,
}: { spot: Spot | null; text: string } & Omit<PanelProps, "detail">) {
  if (!spot) return <>{text}</>;
  const pressed = active !== null && sameRegion(active, spot.region);
  return (
    <button
      type="button"
      className={styles.valueButton}
      aria-pressed={pressed}
      onClick={() => onShow(spot)}
    >
      {text}
      <span className="visually-hidden">{`, show on page ${spot.region.page}`}</span>
    </button>
  );
}


/** The value's "What was read", when there is a value. */
function ReadAt({ detail, value }: { detail: DocumentDetail; value: ExtractedValue | undefined }) {
  if (!value) return null;
  return (
    <WhatWasRead value={value} page={detail.pages.find((p) => p.page_no === value.region.page)} />
  );
}

export function RelatedPanel({ detail }: Pick<PanelProps, "detail">) {
  return (
    <section className={styles.panel} aria-labelledby="related-heading">
      <h2 id="related-heading" className={styles.valuesHeading}>
        Related documents
      </h2>
      {detail.links.length === 0 ? (
        <p className="muted">{untilRead(detail.status, "No other document shares a number with this one.")}</p>
      ) : (
        <ul className={styles.panelList}>
          {groupLinks(detail.links).map(([link, ...more]) => (
            <li key={link.id}>
              <Link href={`/documents/${link.id}`} className={styles.fieldCell}>
                {link.display_name}
              </Link>
              <div className={styles.pageNote}>
                {[
                  docTypeLabel(link.doc_type),
                  ...[link, ...more].map((shared) => linkText(shared, detail.values)),
                ].join(" · ")}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function InvoicePanel({ detail, active, onShow }: PanelProps) {
  const invoice = detail.invoice;
  if (invoice === null) return null;
  const chip = checkChip(invoice);
  const cell = (record: string, field: string, text: string | null) =>
    text === null ? (
      "—"
    ) : (
      <SpotButton
        spot={invoiceSpot(detail.values, record, field)}
        text={text}
        active={active}
        onShow={onShow}
      />
    );
  return (
    <section className={styles.panel} aria-labelledby="invoice-heading">
      <h2 id="invoice-heading" className={styles.valuesHeading}>
        Invoice
      </h2>
      <p>
        <span className={labelBadgeClass(chip.label)}>
          <Icon name={labelIcon(chip.label)} size={16} />
          {chip.text}
        </span>
      </p>
      {chip.reasons.length > 0 && (
        <ul className={styles.checkReasons} aria-label="Why to verify it">
          {chip.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      <dl className={styles.facts}>
        {INVOICE_FIELDS.map(([name, stored]) => (
          <div key={name} className={styles.factRow}>
            <dt>{fieldText(name)}</dt>
            <dd>
              {cell("document", stored, invoice[name])}
              {invoice[name] !== null && (
                <ReadAt
                  detail={detail}
                  value={detail.values.find((v) => v.record_key === "document" && v.field === stored)}
                />
              )}
            </dd>
          </div>
        ))}
      </dl>
      {invoice.lines.length > 0 && (
        <div className={table.reflowWrap}>
          <table className={`table ${table.reflow}`} role="table">
            <caption className="visually-hidden">
              Invoice lines. Pick a cell to show it on its page.
            </caption>
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">#</th>
                {LINE_FIELDS.map((field) => (
                  <th key={field} scope="col" role="columnheader" className={MONEY_LINE_FIELDS.has(field) ? "num" : undefined}>
                    {fieldText(field)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody role="rowgroup">
              {invoice.lines.map((line) => (
                <tr key={line.line_no} role="row">
                  <th scope="row" role="rowheader" className="tabular">
                    {line.line_no}
                  </th>
                  {LINE_FIELDS.map((field) => (
                    <td key={field} role="cell" data-label={fieldText(field)} className={MONEY_LINE_FIELDS.has(field) ? "num" : undefined}>
                      {cell(`line:${line.line_no}`, field, line[field])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** PRD 5 U5: the document's reading problems by page, with jump links; the shown page's marks as buttons. */
export function AttentionPanel({
  status,
  pages,
  current,
  marks,
  onPage,
  onMark,
  hot = null,
  onHot = () => undefined,
  fix = null,
}: {
  /** The shown page's fix (PageProblems inline), under its reasons: one box (24 Oct 2026). */
  fix?: ReactNode;
  status: DocumentStatus;
  pages: PageSummary[];
  current: number | null; // the page shown
  marks: ReadingMark[]; // the shown page's marks
  onPage: (pageNo: number) => void;
  onMark: (mark: ReadingMark, name: string) => void;
  hot?: number | null; // the mark pointed at, here or on the page (its index on the page)
  onHot?: (index: number | null) => void;
}) {
  const problemPages = attentionPages(pages);
  const shownMarks = current === null ? [] : marksOnPage(marks, current); // as numbered on the page
  const lead = toCheck(problemPages);
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
                className={styles.valueButton}
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

/** The Needs attention tab's count (DD6): the reading problems still to check, as "N to check" says. */
export const attentionCount = (pages: PageSummary[]) => toCheck(attentionPages(pages))?.count ?? 0;

/**
 * The Attention panel's "N to check" and its lead line: the problems on pages not kept as
 * is (kept ones are off the list); null when there are none.
 */
function toCheck(problemPages: AttentionPage[]): { count: number; text: string } | null {
  const open = problemPages.filter((item) => !item.kept);
  const count = open.reduce((sum, item) => sum + item.lines.length, 0);
  if (count === 0) return null;
  const where = open.length === 1 ? `page ${open[0]?.pageNo}` : `${open.length.toLocaleString("en-IN")} pages`;
  const them = count === 1 ? "it" : "them";
  const problems = count === 1 ? "1 reading problem" : `${count.toLocaleString("en-IN")} reading problems`;
  return {
    count,
    text: `${problems} on ${where}. Check ${them} on the ${open.length === 1 ? "page" : "pages"} before you rely on ${them}.`,
  };
}
