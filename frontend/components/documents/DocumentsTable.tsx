"use client";

// The list of this customer's documents. Names and status messages are shown
// as plain text. Delete asks for a second click before anything is removed.

import Link from "next/link";
import { useState } from "react";
import { ApiError, deleteDocument } from "@/lib/api";
import { formatBytes, formatDateTime, formatRelative, typeLabel } from "@/lib/format";
import type { DocumentSummary, Me } from "@/lib/types";
import { canDelete } from "./logic";
import { StatusChip } from "./StatusChip";
import styles from "./Documents.module.css";

interface Props {
  items: DocumentSummary[];
  me: Me;
  now: number;
  onDeleted: () => void;
}

export function DocumentsTable({ items, me, now, onDeleted }: Props) {
  return (
    <div className={styles.tableWrap}>
      <table className="table">
        <caption className="visually-hidden">Your documents</caption>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Type</th>
            <th scope="col">Size</th>
            <th scope="col">Uploaded</th>
            <th scope="col">By</th>
            <th scope="col">Status</th>
            <th scope="col">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((document) => (
            <DocumentRow
              key={document.id}
              document={document}
              now={now}
              deletable={canDelete(me, document)}
              onDeleted={onDeleted}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DocumentRow({
  document,
  now,
  deletable,
  onDeleted,
}: {
  document: DocumentSummary;
  now: number;
  deletable: boolean;
  onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteDocument(document.id);
      onDeleted();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not delete.");
      setBusy(false);
      setConfirming(false);
    }
  }

  return (
    <tr>
      <td className={styles.nameCell}>
        <Link href={`/documents/${document.id}`}>{document.display_name}</Link>
      </td>
      <td>{typeLabel(document.detected_type)}</td>
      <td className={styles.nowrap}>{formatBytes(document.size_bytes)}</td>
      <td className={styles.nowrap}>
        <time dateTime={document.created_at} title={formatDateTime(document.created_at)}>
          {formatRelative(document.created_at, now)}
        </time>
      </td>
      <td>{document.uploaded_by_name ?? "—"}</td>
      <td>
        <div className={styles.status}>
          <StatusChip status={document.status} />
          <span className="muted">{document.status_message}</span>
        </div>
      </td>
      <td className={styles.actions}>
        {deletable && !confirming && (
          <button
            type="button"
            className="btn"
            onClick={() => setConfirming(true)}
            aria-label={`Delete ${document.display_name}`}
          >
            Delete
          </button>
        )}
        {deletable && confirming && (
          <div className={styles.confirm} role="group" aria-label="Confirm delete">
            <span>Delete this document for everyone?</span>
            <div className="row">
              <button type="button" className="btn btn-danger" onClick={remove} disabled={busy}>
                {busy ? "Deleting…" : "Yes, delete"}
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => setConfirming(false)}
                disabled={busy}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {error && (
          <span className="error-text" role="alert">
            {error}
          </span>
        )}
      </td>
    </tr>
  );
}
