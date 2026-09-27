"use client";

// The audit log, newest first, a page at a time: one table, reflowed into
// cards below 768 px. Details hold only ids, statuses and counts, and are
// shown as plain text.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError, listAudit } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";
import { pageInfo } from "@/components/documents/logic";
import table from "@/components/documents/Table.module.css";
import { AUDIT_PAGE_SIZE, describeAction, describeDetails } from "./logic";
import styles from "./Admin.module.css";

const SKELETON_ROWS = [0, 1, 2, 3, 4];

interface AuditPage {
  offset: number;
  items: AuditEntry[];
  total: number;
}

export function ActivityPanel() {
  const [offset, setOffset] = useState(0);
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
      <h2 id="activity-heading" className={styles.sectionHeading}>
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
                <td role="cell" data-label="When" className={`${styles.nowrap} tabular`}>
                  <time dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time>
                </td>
                <td role="cell" data-label="Who">
                  {entry.actor_name ?? "—"}
                </td>
                <td role="cell" data-label="What" className={styles.strong}>
                  {describeAction(entry.action)}
                </td>
                <td role="cell" data-label="On" className={styles.wrap}>
                  {entry.target_type ?? "—"}
                  {entry.target_id && (
                    <span className={`muted ${styles.id}`}> {entry.target_id}</span>
                  )}
                </td>
                <td role="cell" data-label="Details" className={`${styles.wrap} ${styles.details}`}>
                  {describeDetails(entry.details) || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
                onClick={() => setOffset(Math.max(0, page.offset - AUDIT_PAGE_SIZE))}
              >
                <Icon name="chevron-left" />
                Previous
              </button>
              <button
                type="button"
                className="btn"
                disabled={!pages.hasNext || page.offset !== offset}
                onClick={() => setOffset(page.offset + AUDIT_PAGE_SIZE)}
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
