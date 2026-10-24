"use client";

// Admin → Answer quality → What Kaagaz learned (PRD 6 F3): documents in answers marked Useful
// in the last 90 days, with how many people and when; no question, no name. Remove stops it
// (audited). One table, reflowed into cards below 48rem.

import Link from "next/link";
import { useEffect, useState } from "react";
import table from "@/components/documents/Table.module.css";
import { WrapName } from "@/components/documents/WrapName";
import { Icon } from "@/components/ui/Icon";
import { Toast } from "@/components/ui/Toast";
import { listLearned, removeLearned } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { LearnedDocument } from "@/lib/types";
import { retryErrorText } from "./logic";
import styles from "./Admin.module.css";

export const LEARNED_TEXT = {
  heading: "What Kaagaz learned",
  intro:
    "Documents in answers people marked Useful. Kaagaz puts them one place higher for similar questions: for the person who marked them, and for everyone once 2 or more people have marked the same page for similar questions.",
  empty: "Nothing learned yet.",
  loading: "Loading what Kaagaz learned…",
  failed: "Could not load what Kaagaz learned.",
  removed: "Removed. Kaagaz no longer puts this document higher.",
} as const;

// Per document, on any page and any question: the server counts so (db/answer_quality.py
// learned), while the lift for everyone counts per page and per question's words (pins.py).
export const peopleText = (n: number) => `${n} ${n === 1 ? "person" : "people"}, on any question`;

export function LearnedList() {
  const [attempt, setAttempt] = useState(0);
  const [items, setItems] = useState<{ attempt: number; list: LearnedDocument[] } | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listLearned().then(
      (reply) => {
        if (!cancelled) setItems({ attempt, list: reply.items });
      },
      (caught: unknown) => {
        if (!cancelled) setError({ attempt, message: retryErrorText(caught) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const remove = (item: LearnedDocument) =>
    removeLearned(item.document_id).then(
      () => {
        setItems((now) => now && { ...now, list: now.list.filter((each) => each.document_id !== item.document_id) });
        setToast(LEARNED_TEXT.removed);
      },
      (caught: unknown) => setToast(retryErrorText(caught)),
    );

  return (
    <>
      <LearnedView
        items={items?.attempt === attempt ? items.list : null}
        error={error?.attempt === attempt ? error.message : null}
        onRetry={() => setAttempt((n) => n + 1)}
        onRemove={(item) => void remove(item)}
      />
      <Toast message={toast} onClose={() => setToast(null)} />
    </>
  );
}

/** The list itself, from props only (tests/admin-answer-quality.test.ts). */
export function LearnedView({
  items,
  error,
  onRetry,
  onRemove,
}: {
  items: LearnedDocument[] | null;
  error: string | null;
  onRetry(): void;
  onRemove(item: LearnedDocument): void;
}) {
  return (
    <section className={styles.section} aria-labelledby="learned-heading">
      <h2 id="learned-heading" className={styles.sectionHeading}>
        {LEARNED_TEXT.heading}
      </h2>
      <p className="muted">{LEARNED_TEXT.intro}</p>
      {error ? (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>{LEARNED_TEXT.failed}</p>
            <p>{error}</p>
          </div>
          <button type="button" className="btn" onClick={onRetry}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      ) : null}
      {!items && !error ? (
        <p className="delayed muted" role="status">
          {LEARNED_TEXT.loading}
        </p>
      ) : null}
      {items && items.length === 0 ? <p className={styles.emptyLine}>{LEARNED_TEXT.empty}</p> : null}
      {items && items.length > 0 ? (
        <div className={`${table.reflowWrap} ${styles.tableScroll}`}>
          <table className={`table ${table.reflow}`} role="table">
            <caption className="visually-hidden">What Kaagaz learned, newest first</caption>
            <thead role="rowgroup">
              <tr role="row">
                {["Document", "Marked Useful by", "Last marked", "Remove"].map((name) => (
                  <th key={name} scope="col" role="columnheader">
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody role="rowgroup">
              {items.map((item) => (
                <tr key={item.document_id} role="row">
                  <td role="cell" data-label="Document" className={`${styles.strong} ${styles.wrap}`}>
                    <Link href={`/documents/${item.document_id}`}>
                      <WrapName name={item.document_name} />
                    </Link>
                  </td>
                  <td role="cell" data-label="Marked Useful by" className="tabular">
                    {peopleText(item.people)}
                  </td>
                  <td role="cell" data-label="Last marked">
                    {formatDate(item.last_marked_at)}
                  </td>
                  <td role="cell" data-label="Remove">
                    <button type="button" className="btn" onClick={() => onRemove(item)}>
                      Remove
                      <span className="visually-hidden">{` ${item.document_name}`}</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
