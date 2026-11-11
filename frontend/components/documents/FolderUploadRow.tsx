"use client";

// One folder upload in the uploads tray (#484): its line and bar, Stop, Continue or Choose “A”
// again, the files not sent from this tab with Retry, and the server's lists of what needs
// attention and what was already in Kaagaz. Not a live region: UploadArea.tsx's status line speaks.

import Link from "next/link";
import { useState } from "react";
import type { ReactNode } from "react";
import { ApiError, restoreDocument, updateDocument } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { FolderItemsPage, FolderUploadItem, UploadLimits } from "@/lib/types";
import { BusyLabel } from "./BusyLabel";
import { shortPathLabel } from "./folderTree";
import type { FolderTree } from "./folderTree";
import { attentionOf, folderLine, itemProblemText, readingLine } from "./folderUpload";
import type { FolderUploadView } from "./folderUpload";
import { uploadStateText } from "./logic";
import styles from "./Documents.module.css";

/** A file this tab could not send, and whether sending it again may help. */
export interface NotSentRow {
  item: FolderUploadItem;
  message: string;
  retry: boolean;
}

interface FolderRowActions {
  stop(): void;
  resume(): void; // this tab still holds the files
  chooseAgain(): void; // pick the folder again (after a reload)
  retry(itemId: string): void;
  retryAll(): void;
  clear(): void;
  loadItems(view: "attention" | "duplicate", after: number): Promise<FolderItemsPage>;
}

const where = (tree: FolderTree, folderId: string | null) =>
  folderId === null ? "All documents" : shortPathLabel(tree, folderId) || "a folder";

export function FolderUploadRow({
  view,
  held,
  notSent,
  tree,
  limits,
  actions,
}: {
  view: FolderUploadView;
  held: boolean;
  notSent: { rows: NotSentRow[]; total: number };
  tree: FolderTree;
  limits: UploadLimits;
  actions: FolderRowActions;
}) {
  const uploaded = view.stored + view.duplicates;
  const sending = view.phase === "sending";
  const busy = sending || view.phase === "stopping"; // no Retry until the files being sent are in
  // "Add new files only", while listing: the bar shows the files checked.
  const checked = view.phase === "registering" ? view.checked : null;
  const meter = checked ?? uploaded;
  const retryable = notSent.rows.some((row) => row.retry);
  // Production QA (5 Nov 2026): a stopped row, or one to choose again, can be cleared too; and
  // one stopping (review of #564): a file being sent may never finish (offline, a server that
  // never answers), so the row is never left with no button. Its run's later events are ignored.
  const settled = ["done", "refused", "stopped", "needs_pick", "stopping"].includes(view.phase);
  const reading = readingLine(view);
  const attention = attentionOf(view);
  // Each button names its folder for a screen reader, as Clear does (production QA, 5 Nov 2026).
  const named = <span className="visually-hidden">{` folder “${view.rootName}”`}</span>;
  return (
    <div className={styles.batch}>
      <div className={styles.batchHead}>
        <p className={styles.batchSummary}>{folderLine(view)}</p>
      </div>
      {view.message && <p className={`error-text ${styles.small}`}>{view.message}</p>}
      {view.total > 0 && view.phase !== "refused" && (
        <div className={styles.progressRow}>
          <progress
            className={styles.readMeter}
            max={view.total}
            value={meter}
            aria-label={`Files of “${view.rootName}” ${checked === null ? "uploaded" : "checked"}`}
            aria-valuetext={`${formatNumber(meter)} of ${formatNumber(view.total)}`}
          />
        </div>
      )}
      {reading && <p className={`muted ${styles.small}`}>{reading}</p>}
      {view.held > 0 && view.heldMessage && <p className={`muted ${styles.small}`}>{view.heldMessage}</p>}
      {view.sending.length > 0 && (
        <ul className={styles.uploads}>
          {view.sending.map((row) => (
            <li key={row.itemId} className={`muted ${styles.small}`}>
              {row.waiting
                ? `${row.name}: ${uploadStateText({ kind: "offline", fraction: row.fraction, server: row.waiting === "server" })}`
                : `Sending ${row.name}`}
            </li>
          ))}
        </ul>
      )}
      <div className={styles.uploadsActions}>
        {(sending || view.phase === "planning" || view.phase === "registering") && (
          <button type="button" className="btn" onClick={actions.stop}>
            Stop{named}
          </button>
        )}
        {view.phase === "stopping" && (
          <button type="button" className="btn" disabled>
            {view.sending.length > 0 ? "Stopping after the files being sent…" : "Stopping…"}
            {named}
          </button>
        )}
        {view.phase === "stopped" && held && (
          <button type="button" className="btn" onClick={actions.resume}>
            Continue upload{named}
          </button>
        )}
        {(view.phase === "needs_pick" || (view.phase === "stopped" && !held)) && (
          <button type="button" className="btn" onClick={actions.chooseAgain}>
            Choose “{view.rootName}” again
          </button>
        )}
        {retryable && !busy && held && (
          <button type="button" className="btn" onClick={actions.retryAll}>
            Retry all failed{named}
          </button>
        )}
        {settled && (
          <button type="button" className="btn btn-quiet" onClick={actions.clear}>
            Clear{named}
          </button>
        )}
      </div>
      {notSent.total > 0 && (
        <ul className={styles.uploads} aria-label="Not sent yet">
          {notSent.rows.map((row) => (
            <li key={row.item.id} className={styles.upload} data-state="error">
              <div className={styles.uploadBody}>
                <span className={styles.fileName}>{row.item.name}</span>
                <span className={`muted ${styles.small}`}>In {where(tree, row.item.folder_id)}</span>
                <p className={`error-text ${styles.small}`}>{row.message}</p>
                {row.retry && held && !busy && (
                  <button type="button" className={`btn ${styles.inlineAction}`} onClick={() => actions.retry(row.item.id)}>
                    Retry<span className="visually-hidden">{` ${row.item.name}`}</span>
                  </button>
                )}
              </div>
            </li>
          ))}
          {notSent.total > notSent.rows.length && (
            <li className={`muted ${styles.small}`}>and {formatNumber(notSent.total - notSent.rows.length)} more not sent yet</li>
          )}
        </ul>
      )}
      {attention > 0 && (
        <ItemList
          kind="attention"
          title={`Needs attention (${formatNumber(attention)})`}
          load={actions.loadItems}
          render={(item) => <AttentionItem key={item.id} item={item} tree={tree} limits={limits} />}
        />
      )}
      {view.duplicates > 0 && (
        <ItemList
          kind="duplicate"
          title={`Already in Kaagaz (${formatNumber(view.duplicates)})`}
          load={actions.loadItems}
          render={(item) => <DuplicateItem key={item.id} item={item} tree={tree} />}
        />
      )}
      {attention === 0 && notSent.total === 0 && view.duplicates === 0 && view.phase === "done" && view.reading === 0 && view.held === 0 && (
        <p className={`muted ${styles.small}`}>Every file is in its folder.</p>
      )}
    </div>
  );
}

/** A list read from the server when opened, 500 at a time, with Show more. */
function ItemList({
  kind,
  title,
  load,
  render,
}: {
  kind: "attention" | "duplicate";
  title: string;
  load: FolderRowActions["loadItems"];
  render(item: FolderUploadItem): ReactNode;
}) {
  const [items, setItems] = useState<FolderUploadItem[]>([]);
  const [next, setNext] = useState<number | null>(-1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  function more() {
    if (next === null || busy) return;
    setBusy(true);
    setError(null);
    load(kind, next).then(
      (page) => {
        setItems((before) => [...before, ...page.items]);
        setNext(page.next_after);
        setBusy(false);
      },
      (caught: unknown) => {
        setError(caught instanceof ApiError ? caught.message : "Could not load the list. Check your connection, then try again.");
        setBusy(false);
      },
    );
  }
  return (
    <details
      onToggle={(event) => {
        if (event.currentTarget.open && items.length === 0) more();
      }}
    >
      <summary className={styles.small}>{title}</summary>
      <ul className={styles.uploads}>{items.map(render)}</ul>
      {busy && <p className={`muted ${styles.small}`}>Loading…</p>}
      {error && <p className={`error-text ${styles.small}`}>{error}</p>}
      {!busy && (error || (next !== null && items.length > 0)) && (
        <button type="button" className={`btn btn-quiet ${styles.inlineAction}`} onClick={more}>
          {error ? "Try again" : "Show more"}
        </button>
      )}
    </details>
  );
}

function AttentionItem({ item, tree, limits }: { item: FolderUploadItem; tree: FolderTree; limits: UploadLimits }) {
  const problem = itemProblemText(item, limits);
  return (
    <li className={styles.upload} data-state="error">
      <div className={styles.uploadBody}>
        <span className={styles.fileName}>{item.name}</span>
        <span className={`muted ${styles.small}`}>In {where(tree, item.folder_id)}</span>
        {problem && <p className={`error-text ${styles.small}`}>{problem}</p>}
        {item.document_id && (
          <Link href={`/documents/${item.document_id}`} className={styles.inlineAction}>
            Open<span className="visually-hidden">{` ${item.name}`}</span>
          </Link>
        )}
      </div>
    </li>
  );
}

/** "Already in Kaagaz, in <path>": Open, and Move it here (after asking), or Restore. */
function DuplicateItem({ item, tree }: { item: FolderUploadItem; tree: FolderTree }) {
  const document = item.document;
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!document || !item.document_id) return null;
  const documentId = item.document_id;
  const from = document.in_trash ? "the Trash" : where(tree, document.folder_id);
  const to = where(tree, item.folder_id);
  const here = !document.in_trash && document.folder_id === item.folder_id;

  function run(work: () => Promise<unknown>, said: string) {
    setBusy(true);
    setError(null);
    work().then(
      () => {
        setDone(said);
        setAsking(false);
        setBusy(false);
      },
      (caught: unknown) => {
        setError(caught instanceof ApiError ? caught.message : "Could not do that. Check your connection, then try again.");
        setBusy(false);
      },
    );
  }

  return (
    <li className={styles.upload}>
      <div className={styles.uploadBody}>
        <span className={styles.fileName}>{item.name}</span>
        <p className={styles.small}>{done ?? `Already in Kaagaz, in ${from}.`}</p>
        <Link href={`/documents/${documentId}`} className={styles.inlineAction}>
          Open<span className="visually-hidden">{` ${item.name}`}</span>
        </Link>
        {!done && document.in_trash && (
          <button
            type="button"
            className={`btn ${styles.inlineAction}`}
            aria-disabled={busy ? true : undefined}
            onClick={() => !busy && run(() => restoreDocument(documentId), "Restored from Trash.")}
          >
            <BusyLabel busy={busy} idle="Restore" working="Restoring…" />
          </button>
        )}
        {!done && !document.in_trash && !here && !asking && (
          <button type="button" className={`btn ${styles.inlineAction}`} onClick={() => setAsking(true)}>
            Move it here<span className="visually-hidden">{`: ${item.name}`}</span>
          </button>
        )}
        {asking && (
          <>
            <p className={styles.small}>
              Move “{item.name}” from {from} to {to}? It will no longer be in {from}.
            </p>
            <span className={styles.uploadsActions}>
              <button type="button" className="btn" onClick={() => setAsking(false)} disabled={busy}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => run(() => updateDocument(documentId, { folder_id: item.folder_id }), `Moved to ${to}.`)}
              >
                <BusyLabel busy={busy} idle="Move it" working="Moving…" />
              </button>
            </span>
          </>
        )}
        {error && (
          <p className={`error-text ${styles.small}`} role="alert">
            {error}
          </p>
        )}
      </div>
    </li>
  );
}
