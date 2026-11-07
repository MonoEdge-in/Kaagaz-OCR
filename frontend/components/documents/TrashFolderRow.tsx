"use client";

// A folder in Trash, as one row of the Trash list (#542, Tushar, 7 Nov 2026): its folder icon and
// name (pressed, or the row clicked, it opens read only), where it was, what it holds, when it was
// deleted and by whom. At Trash's top level it has Restore (nothing is lost, so no question) and
// Delete forever (asked first in a dialog, with the password for whoever types one, #538, and
// again after a 403 password_confirm_required); inside an opened folder its folders only open.
// It can be ticked with the documents, for the selection's Restore and Delete forever.

import { useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { flushSync } from "react-dom";
import { Icon } from "@/components/ui/Icon";
import { Icon as KitIcon } from "@/components/ui/Icon/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { ApiError, confirmPassword, deleteTrashedFolderForever, restoreTrashedFolder } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { TrashedFolder } from "@/lib/types";
import { ActionDialog } from "./ActionDialog";
import { BusyLabel } from "./BusyLabel";
import {
  folderForeverQuestion,
  folderKey,
  folderRestoredMessage,
  heldText,
  lostTheRight,
  passwordAgain,
  wasInText,
} from "./trash";
import styles from "./Documents.module.css";
import library from "./Library.module.css";

export interface TrashFolderRowProps {
  folder: TrashedFolder;
  whenShort: string;
  whenSpoken: string;
  /** Restore and Delete forever (Trash's top level); left out, the row only opens. */
  onDone?: (message: string) => void;
  canForever: boolean;
  askPassword: boolean;
  /** It gets a checkbox for the selection. */
  tickable: boolean;
  selected: boolean;
  onToggle(key: string): void;
  onOpen(folder: TrashedFolder): void;
}

export function TrashFolderRow({
  folder,
  whenShort,
  whenSpoken,
  onDone,
  canForever,
  askPassword,
  tickable,
  selected,
  onToggle,
  onOpen,
}: TrashFolderRowProps) {
  const { refresh } = useSession();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState<"restore" | "forever" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [passwordAsked, setPasswordAsked] = useState(false);
  const needsPassword = askPassword || passwordAsked;
  const passwordId = `forever-folder-password-${folder.id}`;

  async function act(kind: "restore" | "forever", run: () => Promise<string>) {
    setBusy(kind);
    setError(null);
    try {
      const message = await run();
      onDone?.(message);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        onDone?.("This folder is no longer in Trash."); // restored or deleted elsewhere: the list is asked for again
        return;
      }
      if (lostTheRight(caught)) {
        refresh();
        setPasswordAsked(false);
      }
      setError(caught instanceof ApiError ? caught.message : "Check your connection, then try again.");
      setBusy(null);
      if (passwordAgain(caught)) {
        flushSync(() => setPasswordAsked(true));
        window.document.getElementById(passwordId)?.focus();
        return;
      }
      setAsking(false);
    }
  }

  function restore() {
    void act("restore", async () => folderRestoredMessage(await restoreTrashedFolder(folder.id)));
  }

  function deleteForever() {
    const typed = needsPassword ? password : null;
    setPassword("");
    void act("forever", async () => {
      if (typed !== null) await confirmPassword(typed);
      await deleteTrashedFolderForever(folder.id);
      return `Deleted ${folder.name} for good.`;
    });
  }

  function cancel() {
    setAsking(false);
    setError(null);
  }

  // The whole row opens the folder, as a document's row opens the document; its controls keep theirs.
  function openRow(event: ReactMouseEvent<HTMLTableRowElement>) {
    if (event.defaultPrevented || event.button !== 0) return;
    if ((event.target as Element).closest("a, button, input, label, dialog, [role='dialog']")) return;
    if (window.getSelection()?.toString()) return;
    onOpen(folder);
  }

  const by = folder.deleted_by_name;
  const dash = (
    <>
      <span aria-hidden="true">—</span>
      <span className="visually-hidden">Not known</span>
    </>
  );
  return (
    <tr role="row" data-id={folderKey(folder.id)} data-selected={selected ? "true" : undefined} onClick={openRow}>
      <td role="cell" className={styles.selectCell} data-empty={tickable ? undefined : "true"}>
        {tickable && (
          <label className={styles.selectHit}>
            <input
              type="checkbox"
              checked={selected}
              onChange={() => onToggle(folderKey(folder.id))}
              aria-label={`Select folder ${folder.name}`}
              title={`Select folder ${folder.name}`}
            />
          </label>
        )}
      </td>
      <td role="cell" className={styles.nameCell}>
        <button type="button" className={`quiet-link ${styles.name} ${styles.folderName}`} onClick={() => onOpen(folder)}>
          <KitIcon name={folder.kind === "department" ? "department" : "folder"} className={styles.folderIcon} />
          <span>
            <span className="visually-hidden">Open folder </span>
            {folder.name}
          </span>
        </button>
        {onDone && <span className={styles.facts}>{wasInText(folder.was_in)}</span>}
      </td>
      <td role="cell" className={styles.statusCell}>
        <span className={styles.statusText}>{heldText(folder)}</span>
      </td>
      <td role="cell" className={`${styles.meta} ${styles.typeCell}`}>
        Folder
      </td>
      <td role="cell" className={`${styles.meta} ${styles.ownerCell}`}>{dash}</td>
      <td role="cell" className={`${styles.meta} ${styles.labelCell}`}>{dash}</td>
      <td role="cell" className={`${styles.meta} ${styles.versionCell}`}>{dash}</td>
      <td role="cell" className={`${styles.meta} ${styles.docDateCell}`}>{dash}</td>
      <td role="cell" className={`${styles.meta} ${styles.uploadedCell}`}>
        <span className="visually-hidden">Trashed </span>
        <time dateTime={folder.deleted_at} title={formatDateTime(folder.deleted_at)} className={styles.when} aria-hidden="true">
          {whenShort}
        </time>
        <span className="visually-hidden">{whenSpoken}</span>
      </td>
      <td role="cell" className={`${styles.meta} ${styles.byCell}`} data-empty={by ? undefined : "true"}>
        {by ? (
          <>
            <span className={styles.byLabel}>Deleted by </span>
            <span className={styles.byName} title={by}>
              {by}
            </span>
          </>
        ) : (
          dash
        )}
      </td>
      <td role="cell" className={styles.actionsCell}>
        {onDone && (
          <span className={styles.folderActions}>
            {folder.can_restore && (
              <button
                type="button"
                className="btn btn-quiet"
                onClick={restore}
                disabled={busy !== null}
                aria-label={`Restore folder ${folder.name}`}
              >
                <Icon name="history" />
                <BusyLabel busy={busy === "restore"} idle="Restore" working="Restoring…" />
              </button>
            )}
            {canForever && (
              <button
                type="button"
                className="btn btn-quiet"
                onClick={() => {
                  setError(null);
                  setAsking(true);
                }}
                disabled={busy !== null}
                aria-label={`Delete folder ${folder.name} forever`}
              >
                <Icon name="trash" />
                Delete forever
              </button>
            )}
          </span>
        )}
        {asking && (
          <ActionDialog title="Delete forever" onClose={cancel} canClose={() => busy === null}>
            <div className={library.dialogForm}>
              <p>{folderForeverQuestion(folder)}</p>
              {needsPassword && (
                <PasswordField
                  id={passwordId}
                  label="Your password"
                  autoComplete="current-password"
                  value={password}
                  onChange={setPassword}
                />
              )}
              {error && (
                <p className="error-text" role="alert">
                  {error}
                </p>
              )}
              <div className={library.formButtons}>
                <button
                  type="button"
                  className={`btn btn-danger ${library.apart}`}
                  onClick={deleteForever}
                  disabled={busy !== null || (needsPassword && password === "")}
                >
                  <BusyLabel busy={busy === "forever"} idle="Delete forever" working="Deleting…" />
                </button>
                <button type="button" className="btn" data-autofocus onClick={cancel} disabled={busy !== null}>
                  Cancel
                </button>
              </div>
            </div>
          </ActionDialog>
        )}
        {error && !asking && (
          <p className={`error-text ${styles.rowError}`} role="alert">
            {error}
          </p>
        )}
      </td>
    </tr>
  );
}
