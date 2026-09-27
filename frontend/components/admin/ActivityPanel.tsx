"use client";

// The audit log, newest first, a page at a time. Details hold only ids,
// statuses and counts, and are shown as plain text.

import { useEffect, useState } from "react";
import { ApiError, listAudit } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { AuditEntry } from "@/lib/types";
import { pageInfo } from "@/components/documents/logic";
import { AUDIT_PAGE_SIZE, describeAction, describeDetails } from "./logic";
import styles from "./Admin.module.css";

export function ActivityPanel() {
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<{ items: AuditEntry[]; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listAudit({ limit: AUDIT_PAGE_SIZE, offset }).then(
      (result) => {
        if (cancelled) return;
        setPage(result);
        setError(null);
      },
      (caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof ApiError ? caught.message : "Could not load the activity.");
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [offset]);

  const pages = page ? pageInfo(offset, page.items.length, page.total) : null;

  return (
    <section className="card stack" aria-labelledby="activity-heading">
      <h2 id="activity-heading">Activity</h2>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {!page && !error && (
        <p className="muted" role="status">
          Loading…
        </p>
      )}
      {page && page.total === 0 && <p className="muted">Nothing has happened yet.</p>}
      {page && page.items.length > 0 && (
        <div className={styles.tableWrap}>
          <table className="table">
            <caption className="visually-hidden">Activity, newest first</caption>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Who</th>
                <th scope="col">What</th>
                <th scope="col">On</th>
                <th scope="col">Details</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((entry) => (
                <tr key={entry.id}>
                  <td className={styles.nowrap}>
                    <time dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time>
                  </td>
                  <td>{entry.actor_name ?? "—"}</td>
                  <td>{describeAction(entry.action)}</td>
                  <td className={styles.wrap}>
                    {entry.target_type ?? "—"}
                    {entry.target_id && <span className="muted"> {entry.target_id}</span>}
                  </td>
                  <td className={styles.wrap}>{describeDetails(entry.details)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages && page && page.total > 0 && (
        <nav className={styles.pager} aria-label="Activity pages">
          <span className="muted" aria-live="polite">
            {pages.from}–{pages.to} of {page.total}
          </span>
          <button
            type="button"
            className="btn"
            disabled={!pages.hasPrevious}
            onClick={() => setOffset(Math.max(0, offset - AUDIT_PAGE_SIZE))}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn"
            disabled={!pages.hasNext}
            onClick={() => setOffset(offset + AUDIT_PAGE_SIZE)}
          >
            Next
          </button>
        </nav>
      )}
    </section>
  );
}
