"use client";

// The list of this customer's documents: one table, reflowed into cards below
// 768 px (Table.module.css). Names and status messages are shown as plain
// text. Delete asks "Delete this document for everyone?" before anything is
// removed; focus goes to Cancel, and back to Delete if it is cancelled.

import Link from "next/link";
import { useCallback, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError, deleteDocument } from "@/lib/api";
import { formatBytes, formatDateTime, formatRelative, typeLabel } from "@/lib/format";
import type { DocumentSummary, Me } from "@/lib/types";
import { canDelete, formatProgressPercent } from "./logic";
import { StatusChip } from "./StatusChip";
import styles from "./Documents.module.css";
import table from "./Table.module.css";

interface Props {
  items: DocumentSummary[];
  me: Me;
  now: number;
  onDeleted: (name: string) => void;
}

export function DocumentsTable({ items, me, now, onDeleted }: Props) {
  const deletableItems = useMemo(() => items.filter((item) => canDelete(me, item)), [items, me]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [deleteProgress, setDeleteProgress] = useState<{ done: number; total: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const visibleIds = useMemo(() => new Set(items.map((item) => item.id)), [items]);
  const visibleSelectedIds = useMemo(
    () => new Set([...selectedIds].filter((id) => visibleIds.has(id))),
    [selectedIds, visibleIds],
  );
  const selectedCount = visibleSelectedIds.size;
  const allSelected = deletableItems.length > 0 && selectedCount === deletableItems.length;
  const partiallySelected = selectedCount > 0 && !allSelected;

  function toggle(id: string) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setError(null);
  }

  function toggleAll() {
    setSelectedIds(allSelected ? new Set() : new Set(deletableItems.map((item) => item.id)));
    setError(null);
  }

  function clearSelection() {
    setSelectedIds(new Set());
    setConfirming(false);
    setError(null);
  }

  async function removeSelected() {
    const ids = Array.from(visibleSelectedIds);
    setBusy(true);
    setDeleteProgress({ done: 0, total: ids.length });
    setError(null);
    try {
      for (const [index, id] of ids.entries()) {
        setDeleteProgress({ done: index, total: ids.length });
        await deleteDocument(id);
        setDeleteProgress({ done: index + 1, total: ids.length });
      }
      const count = ids.length;
      setSelectedIds(new Set());
      setConfirming(false);
      onDeleted(`${count} documents`);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not delete all selected documents. Check your connection, then try again.",
      );
      setConfirming(false);
    } finally {
      setBusy(false);
      setDeleteProgress(null);
    }
  }

  return (
    <>
      {deletableItems.length > 0 && (
        <div className={styles.selectionBar} role="toolbar" aria-label="Document selection actions">
          <label className={styles.selectAll}>
            <input
              type="checkbox"
              checked={allSelected}
              aria-checked={partiallySelected ? "mixed" : allSelected}
              onChange={toggleAll}
              ref={(input) => {
                if (input) input.indeterminate = partiallySelected;
              }}
            />
            <span>
              {allSelected
                ? "All visible documents selected"
                : partiallySelected
                  ? `${selectedCount} selected`
                  : "Select all visible"}
            </span>
          </label>
          {selectedCount > 0 && !confirming && (
            <>
              <button type="button" className="btn btn-danger" onClick={() => setConfirming(true)}>
                <Icon name="trash" />
                Delete selected ({selectedCount})
              </button>
              <button type="button" className="btn btn-quiet" onClick={clearSelection}>
                <Icon name="close" />
                Clear selection
              </button>
            </>
          )}
          {confirming && (
            <div className={styles.bulkConfirm} role="group" aria-label="Confirm bulk deletion">
              <span>Delete {selectedCount} selected documents for everyone?</span>
              <button type="button" className="btn btn-danger" onClick={removeSelected} disabled={busy}>
                <Icon name="trash" />
                {busy ? "Deleting…" : "Delete now"}
              </button>
              <button type="button" className="btn" onClick={clearSelection} disabled={busy}>
                <Icon name="close" />
                Cancel
              </button>
            </div>
          )}
          {busy && deleteProgress && (
            <div className={styles.progressRow} aria-live="polite">
              <progress
                className={styles.progress}
                value={deleteProgress.done}
                max={deleteProgress.total}
                aria-label="Bulk delete progress"
              />
              <span className={`muted tabular ${styles.small}`} aria-hidden="true">
                {formatProgressPercent(deleteProgress.done, deleteProgress.total)}
              </span>
            </div>
          )}
          {error && <p className={`error-text ${styles.selectionError}`} role="alert">{error}</p>}
        </div>
      )}
      <table className={`table ${table.reflow} ${styles.docs}`} role="table">
        <caption className="visually-hidden">Your documents</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader" className={styles.selectColumn}>
              <span className="visually-hidden">Select</span>
            </th>
            <th scope="col" role="columnheader" className={styles.nameColumn}>Name</th>
            <th scope="col" role="columnheader" className={styles.statusColumn}>Status</th>
            <th scope="col" role="columnheader" className={styles.typeColumn}>Type</th>
            <th scope="col" role="columnheader" className={styles.sizeColumn}>Size</th>
            <th scope="col" role="columnheader" className={styles.uploadedColumn}>Uploaded</th>
            <th scope="col" role="columnheader" className={styles.byColumn}>By</th>
            <th scope="col" role="columnheader" className={styles.actionsColumn}>
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody role="rowgroup">
          {items.map((document) => (
            <DocumentRow
              key={document.id}
              document={document}
              now={now}
              deletable={canDelete(me, document)}
              selected={selectedIds.has(document.id)}
              onToggle={() => toggle(document.id)}
              onDeleted={onDeleted}
            />
          ))}
        </tbody>
      </table>
    </>
  );
}

function DocumentRow({
  document,
  now,
  deletable,
  selected,
  onToggle,
  onDeleted,
}: {
  document: DocumentSummary;
  now: number;
  deletable: boolean;
  selected: boolean;
  onToggle: () => void;
  onDeleted: (name: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refocusDelete = useRef(false);

  // Stable callback refs, so each runs once when its button appears.
  const focusOnMount = useCallback((element: HTMLButtonElement | null) => {
    element?.focus();
  }, []);
  const focusDeleteIfAsked = useCallback((element: HTMLButtonElement | null) => {
    if (element && refocusDelete.current) {
      refocusDelete.current = false;
      element.focus();
    }
  }, []);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await deleteDocument(document.id);
      onDeleted(document.display_name);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not delete this document. Check your connection, then try again.",
      );
      setBusy(false);
      refocusDelete.current = true;
      setConfirming(false);
    }
  }

  function cancel() {
    refocusDelete.current = true;
    setConfirming(false);
  }

  const by = document.uploaded_by_name;
  return (
    <tr role="row" data-selected={selected ? "true" : undefined}>
      <td role="cell" className={styles.selectCell} data-empty={deletable ? undefined : "true"}>
        {deletable && (
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggle}
            aria-label={`Select ${document.display_name}`}
            title={`Select ${document.display_name}`}
          />
        )}
      </td>
      <td role="cell" className={styles.nameCell}>
        <Link
          href={`/documents/${document.id}`}
          className={styles.name}
          title={document.display_name}
        >
          {document.display_name}
        </Link>
      </td>
      <td role="cell" className={styles.statusCell}>
        <span className={styles.statusContent}>
          <StatusChip status={document.status} />
          <span className={styles.statusMessage}>{document.status_message}</span>
        </span>
      </td>
      <td role="cell" className={styles.meta}>
        {typeLabel(document.detected_type)}
      </td>
      <td role="cell" className={`${styles.meta} ${styles.nowrap} tabular`}>
        {formatBytes(document.size_bytes)}
      </td>
      <td role="cell" className={`${styles.meta} ${styles.nowrap}`}>
        <time dateTime={document.created_at} title={formatDateTime(document.created_at)}>
          {formatRelative(document.created_at, now)}
        </time>
      </td>
      <td role="cell" className={`${styles.meta} ${styles.byCell}`} data-empty={by ? undefined : "true"}>
        {by ?? (
          <>
            <span aria-hidden="true">—</span>
            <span className="visually-hidden">Not known</span>
          </>
        )}
      </td>
      <td
        role="cell"
        className={styles.actionsCell}
        data-empty={deletable || error ? undefined : "true"}
      >
        {deletable && !confirming && (
          <button
            ref={focusDeleteIfAsked}
            type="button"
            className={`btn btn-quiet ${styles.deleteButton}`}
            onClick={() => setConfirming(true)}
            aria-label={`Delete ${document.display_name}`}
          >
            <Icon name="trash" />
            <span>Delete</span>
          </button>
        )}
        {deletable && confirming && (
          <div className={styles.confirm} role="group" aria-labelledby={`confirm-${document.id}`}>
            <span id={`confirm-${document.id}`}>Delete this document for everyone?</span>
            <div className={styles.confirmButtons}>
              <button type="button" className="btn btn-danger" onClick={remove} disabled={busy}>
                {busy ? (
                  <>
                    <span className="spinner" aria-hidden="true" />
                    Deleting…
                  </>
                ) : (
                  "Yes, delete"
                )}
              </button>
              <button
                ref={focusOnMount}
                type="button"
                className="btn"
                onClick={cancel}
                disabled={busy}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
        {error && (
          <p className={`error-text ${styles.rowError}`} role="alert">
            {error}
          </p>
        )}
      </td>
    </tr>
  );
}
