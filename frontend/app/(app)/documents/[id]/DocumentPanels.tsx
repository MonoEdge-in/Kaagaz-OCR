"use client";

// The panels beside the page (PRD 3 Y1 to Y3): the document card, the related
// documents and the invoice. Each item with a spot is a button that shows it
// on its page, with its label's spot too. Every text came from the document or
// the AI model, so it is shown as plain text; the AI's own words sit under a
// heading that says so.

import Link from "next/link";
import { Icon } from "@/components/ui/Icon";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { labelBadgeClass, labelIcon } from "@/components/viewer/labels";
import {
  INVOICE_FIELDS,
  LINE_FIELDS,
  cardSections,
  checkChip,
  groupLinks,
  invoiceSpot,
  languagesText,
  linkText,
  noCardText,
  spotNote,
} from "@/components/viewer/panels";
import type { Spot } from "@/components/viewer/panels";
import { fieldText } from "@/components/viewer/labels";
import { valueAt } from "@/components/viewer/scanned";
import { sameRegion } from "@/components/viewer/selection";
import { WhatWasRead } from "@/components/viewer/WhatWasRead";
import { docTypeLabel } from "@/lib/format";
import type { DocumentDetail, ExtractedValue, Region } from "@/lib/types";
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

export function CardPanel({ detail, active, onShow }: PanelProps) {
  const card = detail.card;
  return (
    <section className={styles.panel} aria-labelledby="card-heading">
      <h2 id="card-heading" className={styles.valuesHeading}>
        Document card
      </h2>
      {card === null ? (
        <p className="muted">{noCardText(detail.status)}</p>
      ) : (
        <>
          <p className={styles.pageNote}>
            <LabelBadge label="needs_review" /> Picked by the AI model. Check each one on its page.
          </p>
          <dl className={styles.facts}>
            <div className={styles.factRow}>
              <dt>Type</dt>
              <dd>{docTypeLabel(card.doc_type)}</dd>
            </div>
            <div className={styles.factRow}>
              <dt>Languages</dt>
              <dd>{languagesText(card.languages)}</dd>
            </div>
          </dl>
          {card.summary && (
            <>
              <h3 className={styles.panelHeading}>AI summary, check the pages</h3>
              <p className={styles.summary}>{card.summary}</p>
            </>
          )}
          {cardSections(card, detail.values).map((section) => (
            <div key={section.heading}>
              <h3 className={styles.panelHeading}>{section.heading}</h3>
              <ul className={styles.panelList}>
                {section.items.map((item) => (
                  <li key={item.key}>
                    <span className={styles.fieldCell}>{item.name}: </span>
                    <SpotButton spot={item} text={item.text} active={active} onShow={onShow} />
                    <div className={styles.pageNote}>{spotNote(item)}</div>
                    <ReadAt detail={detail} value={valueAt(detail.values, item.region)} />
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

export function RelatedPanel({ detail }: Pick<PanelProps, "detail">) {
  return (
    <section className={styles.panel} aria-labelledby="related-heading">
      <h2 id="related-heading" className={styles.valuesHeading}>
        Related documents
      </h2>
      {detail.links.length === 0 ? (
        <p className="muted">No other document shares a number with this one.</p>
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
        <ul className={styles.checkReasons} aria-label="Why it needs review">
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
        <div className={styles.linesWrap} tabIndex={0} role="region" aria-label="Invoice lines">
          <table className={`table ${styles.linesTable}`}>
            <caption className="visually-hidden">
              Invoice lines. Pick a cell to show it on its page.
            </caption>
            <thead>
              <tr>
                <th scope="col">#</th>
                {LINE_FIELDS.map((field) => (
                  <th key={field} scope="col">
                    {fieldText(field)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {invoice.lines.map((line) => (
                <tr key={line.line_no}>
                  <th scope="row" className="tabular">
                    {line.line_no}
                  </th>
                  {LINE_FIELDS.map((field) => (
                    <td key={field}>{cell(`line:${line.line_no}`, field, line[field])}</td>
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
