"use client";

// The list of this customer's documents: one table, reflowed into cards when
// the list is narrower than 48rem (Documents.module.css). Names, tags and
// status messages are shown as plain text. Each row has an Actions button
// (DocumentActions.tsx). Move to Trash, and Delete forever in Trash, ask
// first; focus goes to Cancel, and back to Actions if it is cancelled. Restore
// needs no question: nothing is lost. In Trash the list shows when each
// document was trashed, and has no selection bar. Each row is the family's
// current version; a newer one still being read, or one that failed, gets a
// line under the name ("Version 3: ..."), linking to it. The Type column
// gives the file type, and under it what kind of document it is ("Invoice",
// or "Unknown" without the AI model's card: PRD 2, D6).

import Link from "next/link";
import { useCallback, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { newerVersionText } from "@/components/viewer/versions";
import { ApiError, deleteDocument, deleteDocumentForever, restoreDocument } from "@/lib/api";
import { docTypeLabel, formatBytes, formatDateTime, formatRelative, typeLabel } from "@/lib/format";
import type { DocumentSummary, Me, Tag } from "@/lib/types";
import { EditDialog, RowMenu } from "./DocumentActions";
import type { FolderTree } from "./folderTree";
import type { EditAction, RowAction } from "./library";
import { deletedMessage, formatProgressPercent } from "./logic";
import { StatusChip } from "./StatusChip";
import {
  bulkTrashQuestion,
  canDeleteForever,
  deletedForeverMessage,
  documentsCount,
  foreverQuestion,
  restoredMessage,
  trashQuestion,
} from "./trash";
import styles from "./Documents.module.css";
import library from "./Library.module.css";

/** What a row reports back to the screen. */
export interface RowEvents {
  /** Moved to Trash, restored or deleted for good: announce it and ask for the list again. */
  onRemoved(message: string): void;
  /** Renamed, moved or tagged: announce it and ask for the list again. */
  onSaved(message: string): void;
  /** After a move the row may be gone: focus the list's heading. */
  onFocusList(): void;
  onTagCreated(): void;
  /** "Upload new version": the screen opens the file picker for this document. */
  onUploadVersion(document: DocumentSummary): void;
}

interface Props extends RowEvents {
  items: DocumentSummary[];
  inTrash: boolean;
  me: Me;
  now: number;
  tree: FolderTree;
  tags: Tag[] | null;
}

export function DocumentsTable({ items, inTrash, me, now, tree, tags, ...events }: Props) {
  const { onRemoved } = events;
  const canForever = canDeleteForever(me);
  const deletableItems = useMemo(() => items.filter((item) => item.can_delete), [items]);
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
      setSelectedIds(new Set());
      setConfirming(false);
      onRemoved(deletedMessage(documentsCount(ids.length)));
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not move all selected documents to Trash. Check your connection, then try again.",
      );
      setConfirming(false);
    } finally {
      setBusy(false);
      setDeleteProgress(null);
    }
  }

  return (
    <>
      {!inTrash && deletableItems.length > 0 && (
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
                Move to Trash ({selectedCount})
              </button>
              <button type="button" className="btn btn-quiet" onClick={clearSelection}>
                <Icon name="close" />
                Clear selection
              </button>
            </>
          )}
          {confirming && (
            <div className={styles.bulkConfirm} role="group" aria-label="Confirm moving to Trash">
              <span>{bulkTrashQuestion(selectedCount)}</span>
              <button type="button" className="btn btn-danger" onClick={removeSelected} disabled={busy}>
                <Icon name="trash" />
                {busy ? "Moving…" : "Move now"}
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
                aria-label="Moving to Trash"
              />
              <span className={`muted tabular ${styles.small}`} aria-hidden="true">
                {formatProgressPercent(deleteProgress.done, deleteProgress.total)}
              </span>
            </div>
          )}
          {error && <p className={`error-text ${styles.selectionError}`} role="alert">{error}</p>}
        </div>
      )}
      <table className={`table ${styles.docs}`} role="table">
        <caption className="visually-hidden">{inTrash ? "Documents in Trash" : "Your documents"}</caption>
        <thead role="rowgroup">
          <tr role="row">
            <th scope="col" role="columnheader" className={styles.selectColumn}>
              <span className="visually-hidden">Select</span>
            </th>
            <th scope="col" role="columnheader" className={styles.nameColumn}>Name</th>
            <th scope="col" role="columnheader" className={styles.statusColumn}>Status</th>
            <th scope="col" role="columnheader" className={styles.typeColumn}>Type</th>
            <th scope="col" role="columnheader" className={styles.sizeColumn}>Size</th>
            <th scope="col" role="columnheader" className={styles.uploadedColumn}>
              {inTrash ? "Trashed" : "Uploaded"}
            </th>
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
              inTrash={inTrash}
              deletable={document.can_delete}
              canForever={canForever}
              selected={selectedIds.has(document.id)}
              onToggle={() => toggle(document.id)}
              tree={tree}
              tags={tags}
              events={events}
            />
          ))}
        </tbody>
      </table>
    </>
  );
}

type Confirming = "trash" | "forever" | null;

function DocumentRow({
  document,
  now,
  inTrash,
  deletable,
  canForever,
  selected,
  onToggle,
  tree,
  tags,
  events,
}: {
  document: DocumentSummary;
  now: number;
  inTrash: boolean;
  deletable: boolean;
  canForever: boolean;
  selected: boolean;
  onToggle: () => void;
  tree: FolderTree;
  tags: Tag[] | null;
  events: RowEvents;
}) {
  const [confirming, setConfirming] = useState<Confirming>(null);
  const [editing, setEditing] = useState<EditAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const actionsButton = useRef<HTMLButtonElement | null>(null);
  const refocusActions = useRef(false);
  const afterEdit = useRef<"actions" | "list">("actions");

  // Stable callback refs, so each runs once when its button appears.
  const focusOnMount = useCallback((element: HTMLButtonElement | null) => {
    element?.focus();
  }, []);
  const holdActions = useCallback((element: HTMLButtonElement | null) => {
    actionsButton.current = element;
    if (element && refocusActions.current) {
      refocusActions.current = false;
      element.focus();
    }
  }, []);

  function pick(action: RowAction) {
    setError(null);
    if (action === "trash" || action === "forever") {
      setConfirming(action);
      return;
    }
    if (action === "version") {
      events.onUploadVersion(document);
      return;
    }
    if (action === "restore") {
      void act(restoreDocument, restoredMessage(document.display_name), "Could not restore this document.");
      return;
    }
    afterEdit.current = "actions";
    setEditing(action);
  }

  function saved(message: string, moved = false) {
    afterEdit.current = moved ? "list" : "actions";
    setEditing(null);
    events.onSaved(message);
  }

  // The dialog is shut: hand focus back to Actions, or to the list after a move.
  function dialogClosed() {
    if (afterEdit.current === "list") events.onFocusList();
    else actionsButton.current?.focus();
  }

  // Move to Trash, Restore or Delete forever: the row leaves this list when it works.
  async function act(call: (id: string) => Promise<unknown>, message: string, failed: string) {
    setBusy(true);
    setError(null);
    try {
      await call(document.id);
      events.onRemoved(message);
    } catch (caught) {
      setError(
        caught instanceof ApiError ? caught.message : `${failed} Check your connection, then try again.`,
      );
      setBusy(false);
      refocusActions.current = true;
      setConfirming(null);
    }
  }

  function confirm() {
    if (confirming === "forever") {
      void act(
        deleteDocumentForever,
        deletedForeverMessage(document.display_name),
        "Could not delete this document.",
      );
    } else {
      void act(
        deleteDocument,
        deletedMessage(document.display_name),
        "Could not move this document to Trash.",
      );
    }
  }

  function cancel() {
    refocusActions.current = true;
    setConfirming(null);
  }

  const by = document.uploaded_by_name;
  // In Trash the time column is when it was trashed; elsewhere, when it was uploaded.
  const when = inTrash && document.deleted_at !== null ? document.deleted_at : document.created_at;
  const question = confirming === "forever" ? foreverQuestion(document) : trashQuestion(document);
  return (
    <tr role="row" data-selected={selected ? "true" : undefined}>
      <td
        role="cell"
        className={styles.selectCell}
        data-empty={deletable && !inTrash ? undefined : "true"}
      >
        {deletable && !inTrash && (
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
        {document.tags.length > 0 && (
          <ul className={`${library.tags} ${library.rowTags}`} aria-label="Tags">
            {document.tags.map((tag) => (
              <li key={tag.id} className={library.tag}>
                {tag.name}
              </li>
            ))}
          </ul>
        )}
        {document.newer_version && (
          <Link href={`/documents/${document.newer_version.id}`} className={styles.newerVersion}>
            {newerVersionText(document.newer_version)}
          </Link>
        )}
      </td>
      <td role="cell" className={styles.statusCell}>
        <span className={styles.statusContent}>
          <StatusChip status={document.status} />
          <span className={styles.statusMessage}>{document.status_message}</span>
        </span>
      </td>
      <td role="cell" className={styles.meta}>
        {typeLabel(document.detected_type)}
        <span className={styles.docType}>{docTypeLabel(document.doc_type)}</span>
      </td>
      <td role="cell" className={`${styles.meta} ${styles.nowrap} tabular`}>
        {formatBytes(document.size_bytes)}
      </td>
      <td role="cell" className={`${styles.meta} ${styles.nowrap}`}>
        {inTrash && <span className="visually-hidden">Trashed </span>}
        <time dateTime={when} title={formatDateTime(when)}>
          {formatRelative(when, now)}
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
      <td role="cell" className={styles.actionsCell}>
        {confirming === null && !busy && (
          <RowMenu
            item={document}
            canForever={canForever}
            onPick={pick}
            toggleRef={holdActions}
          />
        )}
        {confirming === null && busy && (
          <p className={`muted ${styles.small}`} role="status">
            <span className="spinner" aria-hidden="true" /> Restoring…
          </p>
        )}
        {confirming !== null && (
          <div className={styles.confirm} role="group" aria-labelledby={`confirm-${document.id}`}>
            <span id={`confirm-${document.id}`}>{question}</span>
            <div className={styles.confirmButtons}>
              <button type="button" className="btn btn-danger" onClick={confirm} disabled={busy}>
                {busy ? (
                  <>
                    <span className="spinner" aria-hidden="true" />
                    {confirming === "forever" ? "Deleting…" : "Moving…"}
                  </>
                ) : confirming === "forever" ? (
                  "Delete forever"
                ) : (
                  "Move to Trash"
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
        {editing && (
          <EditDialog
            action={editing}
            item={document}
            tree={tree}
            tags={tags}
            onClose={() => setEditing(null)}
            onClosed={dialogClosed}
            onSaved={saved}
            onTagCreated={events.onTagCreated}
          />
        )}
      </td>
    </tr>
  );
}
