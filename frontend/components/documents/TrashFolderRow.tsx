"use client";

// A folder in Trash, as one row of the Trash list (#542, Tushar, 7 Nov 2026): its folder icon and
// name (pressed, or the row clicked, it opens read only), where it was, what it holds, when it was
// deleted and by whom. At Trash's top level it has Restore (nothing is lost, so no question) and
// Delete forever (asked first in a dialog, with the password for whoever types one, #538, and
// again after a 403 password_confirm_required); inside an opened folder its folders only open.
// It can be ticked with the documents, for the selection's Restore and Delete forever.
// Stability pass (Tushar's screenshot, 7 Nov 2026, B16): it is laid out as a document in Trash,
// cell for cell: where it was and what it holds are the facts under its name, Status is a dash,
// and Restore and Delete forever sit behind the same ⋯ menu (RowMenu), inside the actions cell.
// A wrong password keeps the question open with why, focus in the box (B6); once it worked the
// question closes before the list hears it, so the list's heading can take focus (B11). A big
// folder is deleted a part at a time, "Deleting… 400 left" (B18); one that stops part way says
// how many are still in Trash.

import { useCallback, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { flushSync } from "react-dom";
import { Icon as KitIcon } from "@/components/ui/Icon/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { ApiError, confirmPassword, deleteTrashedFolderForever, restoreTrashedFolder } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import { formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import { beginKeycloakReauth, isKeycloakManaged, isKeycloakReauthRequired, keycloakStartFailure } from "@/lib/keycloak";
import type { TrashedFolder } from "@/lib/types";
import { ActionDialog } from "./ActionDialog";
import { BusyLabel } from "./BusyLabel";
import { RowMenu } from "./DocumentActions";
import type { RowAction } from "./library";
import {
  deletedForeverMessage,
  folderForeverQuestion,
  folderKey,
  folderRestoredMessage,
  foreverStoppedText,
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
  const { me, refresh } = useSession();
  const managed = isKeycloakManaged(me);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState<"restore" | "forever" | null>(null);
  const [left, setLeft] = useState<number | null>(null); // B18: files still to delete, once the server says
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [passwordAsked, setPasswordAsked] = useState(false);
  const needsPassword = !managed && (askPassword || passwordAsked);
  const passwordId = `forever-folder-password-${folder.id}`;
  const menuButton = useRef<HTMLButtonElement | null>(null);
  const refocusMenu = useRef(false); // a Restore that failed: focus goes back to the menu once it shows again
  const holdMenu = useCallback((element: HTMLButtonElement | null) => {
    menuButton.current = element;
    if (element && refocusMenu.current) {
      refocusMenu.current = false;
      element.focus();
    }
  }, []);
  // The question shut because it worked: the list takes focus (onDone), not the menu button.
  const shutDone = useRef(false);
  const foreverButton = useRef<HTMLButtonElement | null>(null);

  /** Shut the question and the busy state now, before the list hears it (B11). */
  function finish(message: string) {
    flushSync(() => {
      shutDone.current = asking; // a Restore opened no question: nothing to skip
      setAsking(false);
      setBusy(null);
      setLeft(null);
    });
    onDone?.(message);
  }

  async function act(kind: "restore" | "forever", run: (step: { checking: boolean; left: number | null }) => Promise<string>) {
    setBusy(kind);
    setError(null);
    setLeft(null);
    const step = { checking: false, left: null as number | null };
    try {
      finish(await run(step));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        finish("This folder is no longer in Trash."); // restored or deleted elsewhere: the list is asked for again
        return;
      }
      if (kind === "forever" && managed && isKeycloakReauthRequired(caught)) {
        setBusy(null);
        try {
          await beginKeycloakReauth("delete_forever", window.location.pathname);
          return;
        } catch (failed) {
          setError(keycloakStartFailure(failed));
          return;
        }
      }
      const lost = lostTheRight(caught);
      if (lost) {
        refresh();
        setPasswordAsked(false);
      }
      const why = errorWords(caught);
      setError(step.left === null ? why : `${foreverStoppedText(step.left)} ${why}`);
      setBusy(null);
      // The question stays open, with why: a refused or out-of-date password (asked again, focus
      // in its box), or a delete that stopped part way (Delete forever goes on from there).
      const again = passwordAgain(caught);
      if (again || (step.checking && !lost) || step.left !== null) {
        flushSync(() => {
          if (again) setPasswordAsked(true);
        });
        // Focus waits in the password box, or on Delete forever to go on from where it stopped.
        (window.document.getElementById(passwordId) ?? foreverButton.current)?.focus();
        return;
      }
      refocusMenu.current = kind === "restore";
      setAsking(false);
    }
  }

  function pick(action: RowAction) {
    setError(null);
    if (action === "restore") void act("restore", async () => folderRestoredMessage(await restoreTrashedFolder(folder.id)));
    if (action === "forever") setAsking(true);
  }

  function deleteForever() {
    const typed = needsPassword ? password : null;
    setPassword("");
    void act("forever", async (step) => {
      if (typed !== null || managed) {
        step.checking = true;
        await confirmPassword(typed ?? undefined);
        step.checking = false;
      }
      await deleteTrashedFolderForever(folder.id, (remaining) => {
        step.left = remaining;
        setLeft(remaining);
      });
      return deletedForeverMessage(folder.name);
    });
  }

  function cancel() {
    // Stopped part way: some files are gone, so the list is asked for again and the toast says
    // how many are left, instead of the row's old counts with no trace of it.
    if (left !== null && busy === null) {
      finish(foreverStoppedText(left));
      return;
    }
    setAsking(false);
    setError(null);
  }

  // The whole row opens the folder, as a document's row opens the document; its controls keep theirs.
  function openRow(event: ReactMouseEvent<HTMLTableRowElement>) {
    if (event.defaultPrevented || event.button !== 0) return;
    if ((event.target as Element).closest("a, button, input, label, dialog, [role='menu'], [role='dialog']")) return;
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
  // The facts under the name, each its own span: the line starts a dot early (Documents.module.css),
  // which clipped bare text's first letter ("as in: Top level").
  const facts = [onDone ? wasInText(folder.was_in) : null, heldText(folder)].filter((fact): fact is string => fact !== null);
  const deleting = left === null ? "Deleting…" : `Deleting… ${left} left`;
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
        <span className={`${styles.facts} ${styles.folderFacts}`} title={facts.join(" · ")}>
          {facts.map((fact) => (
            <span key={fact}>{fact}</span>
          ))}
        </span>
      </td>
      <td role="cell" className={styles.statusCell} data-empty="true">
        <span className={styles.statusContent}>
          <span className={styles.statusLine}>
            <span className={styles.statusText}>{dash}</span>
          </span>
        </span>
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
        {by && (
          <span className={styles.bandBy} title={by}>
            <span className="visually-hidden">Deleted </span>by {by}
          </span>
        )}
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
        {onDone &&
          (busy === "restore" ? (
            <p className={`muted ${styles.small}`} role="status">
              <span className="spinner" aria-hidden="true" /> Restoring…
            </p>
          ) : (
            <RowMenu
              item={{
                id: folder.id,
                display_name: `folder ${folder.name}`,
                status: "ready", // read only for a download, which nothing in Trash offers
                deleted_at: folder.deleted_at,
                can_delete: folder.can_restore,
              }}
              canForever={canForever}
              onPick={pick}
              toggleRef={holdMenu}
            />
          ))}
        {asking && (
          <ActionDialog
            title="Delete forever"
            onClose={cancel}
            onClosed={() => {
              if (shutDone.current) shutDone.current = false;
              else menuButton.current?.focus();
            }}
            canClose={() => busy === null}
          >
            <div className={library.dialogForm}>
              <p>{folderForeverQuestion(folder)}</p>
              {managed && <p className="hint">Company sign-in may ask you to verify first. Nothing is deleted during verification; find this folder again and confirm after returning.</p>}
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
              <p className="visually-hidden" aria-live="polite">
                {busy === "forever" && left !== null ? deleting : ""}
              </p>
              <div className={library.formButtons}>
                <button
                  ref={foreverButton}
                  type="button"
                  className={`btn btn-danger ${library.apart}`}
                  onClick={deleteForever}
                  disabled={busy !== null || (needsPassword && password === "")}
                >
                  <BusyLabel busy={busy === "forever"} idle="Delete forever" working={deleting} />
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
