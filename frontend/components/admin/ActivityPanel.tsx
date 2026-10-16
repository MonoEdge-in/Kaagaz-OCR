"use client";

// The audit log, newest first, a page at a time: one table, reflowed into
// cards below 768 px. What each row was done on, and its details, are shown in
// words, never an id (UI quality council, 15 Oct 2026).

import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError, listAudit } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";
import { pageInfo } from "@/components/documents/logic";
import table from "@/components/documents/Table.module.css";
import { WrapName } from "@/components/documents/WrapName";
import { AUDIT_PAGE_SIZE, describeAction, describeDetails, describeTarget, whoText } from "./logic";
import styles from "./Admin.module.css";

const SKELETON_ROWS = [0, 1, 2, 3, 4];

interface AuditPage {
  offset: number;
  items: AuditEntry[];
  total: number;
}

export function ActivityPanel() {
  const [offset, setOffset] = useState(0);
  const headingRef = useRef<HTMLHeadingElement>(null);
  // The pager's button is disabled while the next page loads: start reading at the heading.
  const turnPage = (next: number) => {
    setOffset(next);
    headingRef.current?.focus();
  };
  const [attempt, setAttempt] = useState(0);
  // The page on screen stays while the next one loads.
  const [page, setPage] = useState<AuditPage | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const key = `${offset}:${attempt}`;

  useEffect(() => {
    let cancelled = false;
    listAudit({ limit: AUDIT_PAGE_SIZE, offset }).then(
      (result) => {
        if (!cancelled) setPage({ offset, items: result.items, total: result.total });
      },
      (caught: unknown) => {
        if (!cancelled) {
          setError({
            key,
            message:
              caught instanceof ApiError
                ? caught.message
                : "Check your internet connection, then try again.",
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [offset, key]);

  const failed = error !== null && error.key === key ? error.message : null;
  const pages = page ? pageInfo(page.offset, page.items.length, page.total) : null;

  return (
    <section className={styles.section} aria-labelledby="activity-heading">
      <h2 id="activity-heading" ref={headingRef} tabIndex={-1} className={styles.sectionHeading}>
        Activity
      </h2>
      {failed && (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>Could not load the activity.</p>
            <p>{failed}</p>
          </div>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {!page && !failed && (
        <>
          <p className="visually-hidden" role="status">
            Loading the activity…
          </p>
          <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
            {SKELETON_ROWS.map((row) => (
              <span key={row} className={`skeleton ${styles.skeletonRow}`} />
            ))}
          </div>
        </>
      )}
      {page && page.total === 0 && <p className={styles.emptyLine}>No activity yet.</p>}
      {page && page.items.length > 0 && (
        <div className={`${table.reflowWrap} ${styles.tableScroll}`}>
          <table className={`table ${table.reflow}`} role="table">
            <caption className="visually-hidden">Activity, newest first</caption>
            <thead role="rowgroup">
              <tr role="row">
                <th scope="col" role="columnheader">
                  When
                </th>
                <th scope="col" role="columnheader">
                  Who
                </th>
                <th scope="col" role="columnheader">
                  What
                </th>
                <th scope="col" role="columnheader">
                  On
                </th>
                <th scope="col" role="columnheader">
                  Details
                </th>
              </tr>
            </thead>
            <tbody role="rowgroup">
              {page.items.map((entry) => (
                <tr key={entry.id} role="row">
                  <td role="cell" data-label="When" className="tabular">
                    <time dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time>
                  </td>
                  <td role="cell" data-label="Who">
                    {whoText(entry)}
                  </td>
                  <td role="cell" data-label="What" className={styles.strong}>
                    <WrapName name={describeAction(entry.action, entry.details)} />
                  </td>
                  <td role="cell" data-label="On" className={styles.wrap}>
                    {describeTarget(entry.target_type)}
                  </td>
                  <td role="cell" data-label="Details" className={`${styles.wrap} ${styles.details}`}>
                    {describeDetails(entry.details) || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages && page && page.total > 0 && (
        <nav className={styles.pager} aria-label="Activity pages">
          <span className="muted tabular" aria-live="polite">
            {pages.from}–{pages.to} of {page.total}
          </span>
          {page.total > AUDIT_PAGE_SIZE && (
            <>
              <button
                type="button"
                className="btn"
                disabled={!pages.hasPrevious || page.offset !== offset}
                onClick={() => turnPage(Math.max(0, page.offset - AUDIT_PAGE_SIZE))}
              >
                <Icon name="chevron-left" />
                Previous
              </button>
              <button
                type="button"
                className="btn"
                disabled={!pages.hasNext || page.offset !== offset}
                onClick={() => turnPage(page.offset + AUDIT_PAGE_SIZE)}
              >
                Next
                <Icon name="chevron-right" />
              </button>
            </>
          )}
        </nav>
      )}
    </section>
  );
}
