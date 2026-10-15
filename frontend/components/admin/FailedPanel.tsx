"use client";

// The failed documents (PRD 3 Z1): documents that failed or can't be read, newest
// first, with the plain message the document screen shows and when each was
// last tried. Retry calls the document's Read again route; a refusal (someone
// already retried it, 409, or too many at once, 429) shows the server's words
// on that row. One table, reflowed into cards below 768 px.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { StatusChip } from "@/components/documents/StatusChip";
import { pageInfo } from "@/components/documents/logic";
import table from "@/components/documents/Table.module.css";
import { WrapName } from "@/components/documents/WrapName";
import { Icon } from "@/components/ui/Icon";
import { listFailed, reprocessDocument } from "@/lib/api";
import type { FailedDocument } from "@/lib/types";
import { FAILED_PAGE_SIZE, lastRunText, retryErrorText } from "./logic";
import styles from "./Admin.module.css";

type Retry = { busy: true } | { busy: false; message: string; started: boolean };

export function FailedPanel() {
  const [offset, setOffset] = useState(0);
  const headingRef = useRef<HTMLHeadingElement>(null);
  // The pager's button is disabled while the next page loads: start reading at the heading.
  const turnPage = (next: number) => {
    setOffset(next);
    headingRef.current?.focus();
  };
  const [attempt, setAttempt] = useState(0);
  const [page, setPage] = useState<{
    offset: number;
    items: FailedDocument[];
    total: number;
  } | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [retries, setRetries] = useState<Record<string, Retry>>({});
  const key = `${offset}:${attempt}`;

  useEffect(() => {
    let cancelled = false;
    listFailed({ limit: FAILED_PAGE_SIZE, offset }).then(
      (result) => {
        if (!cancelled) setPage({ offset, ...result });
      },
      (caught: unknown) => {
        if (!cancelled) setError({ key, message: retryErrorText(caught) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [offset, key]);

  const retry = async (id: string) => {
    setRetries((all) => ({ ...all, [id]: { busy: true } }));
    let done: Retry;
    try {
      await reprocessDocument(id);
      done = { busy: false, started: true, message: "Sent to be read again." };
    } catch (caught) {
      done = { busy: false, started: false, message: retryErrorText(caught) };
    }
    setRetries((all) => ({ ...all, [id]: done }));
  };

  const failed = error !== null && error.key === key ? error.message : null;
  const pages = page ? pageInfo(page.offset, page.items.length, page.total) : null;

  return (
    <section className={styles.section} aria-labelledby="failed-heading">
      <h2 id="failed-heading" ref={headingRef} tabIndex={-1} className={styles.sectionHeading}>
        Failed documents
      </h2>
      {failed && (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>Could not load the failed documents.</p>
            <p>{failed}</p>
          </div>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {!page && !failed && (
        <p className="delayed muted" role="status">
          Loading the failed documents…
        </p>
      )}
      {page && page.total === 0 && (
        <p className={styles.emptyLine}>No failed documents. Every document was read.</p>
      )}
      {page && page.items.length > 0 && (
        <div className={table.reflowWrap}>
          <table className={`table ${table.reflow}`} role="table">
            <caption className="visually-hidden">Failed documents, newest first</caption>
            <thead role="rowgroup">
              <tr role="row">
                {["Document", "Status", "What happened", "Last tried", "Retry"].map((name) => (
                  <th key={name} scope="col" role="columnheader">
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody role="rowgroup">
              {page.items.map((item) => {
                const state = retries[item.id];
                return (
                  <tr key={item.id} role="row">
                    <td
                      role="cell"
                      data-label="Document"
                      className={`${styles.strong} ${styles.wrap}`}
                    >
                      <Link href={`/documents/${item.id}`}>
                        <WrapName name={item.display_name} />
                      </Link>
                    </td>
                    <td role="cell" data-label="Status">
                      <StatusChip status={item.status} />
                    </td>
                    <td role="cell" data-label="What happened" className={styles.wrap}>
                      {item.message}
                    </td>
                    <td role="cell" data-label="Last tried">
                      {lastRunText(item.last_run_at)}
                    </td>
                    <td role="cell" data-label="Retry">
                      <button
                        type="button"
                        className="btn"
                        disabled={state?.busy === true || (state && !state.busy && state.started)}
                        aria-describedby={state && !state.busy ? `retry-${item.id}` : undefined}
                        onClick={() => void retry(item.id)}
                      >
                        {state?.busy ? (
                          <span className="spinner" aria-hidden="true" />
                        ) : (
                          <Icon name="refresh" />
                        )}
                        Retry
                        <span className="visually-hidden">{` ${item.display_name}`}</span>
                      </button>
                      <p id={`retry-${item.id}`} className={styles.wrap} aria-live="polite">
                        {state && !state.busy ? state.message : ""}
                      </p>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {pages && page && page.total > FAILED_PAGE_SIZE && (
        <nav className={styles.pager} aria-label="Failed documents pages">
          <span className="muted tabular">
            {pages.from}–{pages.to} of {page.total}
          </span>
          <button
            type="button"
            className="btn"
            disabled={!pages.hasPrevious || page.offset !== offset}
            onClick={() => turnPage(Math.max(0, page.offset - FAILED_PAGE_SIZE))}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn"
            disabled={!pages.hasNext || page.offset !== offset}
            onClick={() => turnPage(page.offset + FAILED_PAGE_SIZE)}
          >
            Next
          </button>
        </nav>
      )}
    </section>
  );
}
