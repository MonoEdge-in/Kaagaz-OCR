"use client";

// The document screen's menu (UI audit DD2, 16 Oct 2026), at the end of the title row:
// Read again (a reviewer or an admin; ReadAgain.tsx asks first), Rename, Move to folder,
// Tags and Move to Trash; in Trash, Restore. The same menu, dialogs and words as a row's
// in the documents list (DocumentActions.tsx). The folders and tags load the first time
// a dialog needs them. Move to Trash asks first, Cancel focused; focus goes back to the
// menu button when a dialog shuts. The server checks every rule again.
// PRD 7 (#604): Give access (an admin session) and Who can see this (an admin session, or the Head
// of the file's department), before Move to Trash.
// PRD 7 (PR 13): an Admin's Lock, after the menu's own items (LockDialog.tsx); a locked file
// has its own screen, with Unlock (LockedDocument.tsx). Locked, this menu goes with the screen:
// once the file is loaded again, focus goes to the locked screen's heading (onLocked).
// PRD 9 F-09: Make it a version of… (Admins in an admin session and Management, mayLinkVersions)
// opens the link dialog with this file fixed; linked, it is said with Undo.

import { useCallback, useEffect, useMemo, useState } from "react";
import type { RefObject } from "react";
import { accessAbilities } from "@/components/access/abilities";
import { AudiencePanel } from "@/components/access/AudiencePanel";
import { GiveAccessDialog } from "@/components/access/GiveAccessDialog";
import { BusyLabel } from "@/components/documents/BusyLabel";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { EditDialog, RowMenu } from "@/components/documents/DocumentActions";
import { LockDialog } from "@/components/documents/LockDialog";
import { buildTree } from "@/components/documents/folderTree";
import type { EditAction, RowAction } from "@/components/documents/library";
import libraryStyles from "@/components/documents/Library.module.css";
import { copyFor, detailPath } from "@/components/documents/copyPath";
import { deletedMessage } from "@/components/documents/logic";
import { placedMessage, restoredMessage, trashQuestion } from "@/components/documents/trash";
import { LOCK_TEXT } from "@/components/viewer/locked";
import { LinkDialog } from "@/components/versions/LinkDialog";
import { fromSummary, mayLinkVersions } from "@/components/versions/link";
import { ApiError, deleteDocument, listFolders, listTags, restoreDocument } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DocumentDetail, Folder, Tag } from "@/lib/types";
import type { MoreItem } from "@/components/documents/DocumentActions";

const failed = (caught: unknown) =>
  caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.";

export function DocumentMenu({
  buttonRef,
  detail,
  readAgain,
  onReadAgain,
  onChanged,
  onLocked,
  more,
}: {
  buttonRef: RefObject<HTMLButtonElement | null>; // the menu's button: focus comes back to it
  /** The review's actions (documentMoreItems), after the menu's own. */
  more?: readonly MoreItem[];
  detail: DocumentDetail;
  readAgain: boolean; // canReprocess
  onReadAgain(): void;
  /** Renamed, moved, tagged, trashed, restored or linked (with its Undo): say it and load it again. */
  onChanged(message: string, undo?: () => Promise<unknown>): void;
  /** Locked (PR 13): say it, load it again, and focus its locked screen's heading; else onChanged. */
  onLocked?(message: string): void;
}) {
  const { me } = useSession();
  const [editing, setEditing] = useState<EditAction | null>(null);
  const [sharing, setSharing] = useState<"give" | "audience" | null>(null);
  const [trashing, setTrashing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null); // #730: what was copied, and how to use it
  const [folders, setFolders] = useState<Folder[] | null>(null);
  const [tags, setTags] = useState<Tag[] | null>(null);
  const [locking, setLocking] = useState(false);
  const [linking, setLinking] = useState(false);
  const canLock = me.user.role === "admin" && me.session.scope === "admin" && detail.deleted_at === null;
  const items = canLock
    ? [...(more ?? []), { label: LOCK_TEXT.menu, hint: LOCK_TEXT.menuHint, onSelect: () => setLocking(true) }]
    : more;
  const holdToggle = useCallback(
    (element: HTMLButtonElement | null) => {
      buttonRef.current = element;
    },
    [buttonRef],
  );
  const tree = useMemo(() => buildTree(folders ?? []), [folders]);
  const abilities = accessAbilities(me, { departmentId: detail.department?.id ?? null });
  const needsLists = editing !== null && folders === null;

  useEffect(() => {
    if (!needsLists) return;
    let cancelled = false;
    listFolders().then(
      (result) => {
        if (!cancelled) setFolders(result.items);
      },
      (caught: unknown) => {
        if (cancelled) return;
        setEditing(null);
        setError(failed(caught));
      },
    );
    listTags().then(
      (result) => {
        if (!cancelled) setTags(result.items);
      },
      () => undefined, // the Tags dialog says it is still loading
    );
    return () => {
      cancelled = true;
    };
  }, [needsLists]);

  function pick(action: RowAction) {
    setError(null);
    setCopied(null);
    if (action === "read") onReadAgain();
    else if (action === "trash") setTrashing(true);
    else if (action === "restore") {
      restoreDocument(detail.id).then(
        ({ placed }) => onChanged([restoredMessage(detail.display_name), placed && placedMessage(placed)].filter(Boolean).join(" ")),
        (caught: unknown) => setError(failed(caught)),
      );
    } else if (action === "rename" || action === "move" || action === "tags") setEditing(action);
    else if (action === "give" || action === "audience") setSharing(action);
    else if (action === "link") setLinking(true);
    else if (action === "copyPath" || action === "copyLink") {
      // #730: the line says how to use it, or that the browser would not copy.
      void copyFor(action, detailPath(detail), detail.id).then(({ copied, line }) => (copied ? setCopied(line) : setError(line)));
    }
  }

  function trash() {
    setBusy(true);
    deleteDocument(detail.id).then(
      () => {
        setBusy(false);
        setTrashing(false);
        onChanged(deletedMessage(detail.display_name));
      },
      (caught: unknown) => {
        setBusy(false);
        setError(failed(caught));
      },
    );
  }

  return (
    <>
      <RowMenu
        item={detail}
        canForever={false}
        place="document"
        readAgain={readAgain}
        more={items}
        onPick={pick}
        toggleRef={holdToggle}
        access={abilities}
        linkVersions={mayLinkVersions(me) && detail.locked !== true}
      />
      {linking && (
        <LinkDialog
          files={[fromSummary(detail)]}
          start="pick"
          fixed={detail.id}
          onClose={() => setLinking(false)}
          onLinked={(done) => {
            setLinking(false);
            onChanged(done.message, done.undo);
          }}
        />
      )}
      {sharing === "give" && (
        <GiveAccessDialog
          files={[detail]}
          myId={me.user.id}
          onClose={() => setSharing(null)}
          onClosed={() => buttonRef.current?.focus()}
          onGiven={(message) => {
            setSharing(null);
            onChanged(message);
          }}
        />
      )}
      {sharing === "audience" && (
        <AudiencePanel
          target={{ kind: "document", id: detail.id, name: detail.display_name }}
          myId={me.user.id}
          abilities={abilities}
          onClose={() => setSharing(null)}
          onClosed={() => buttonRef.current?.focus()}
          onChanged={onChanged}
        />
      )}
      {copied && (
        <p className="muted" role="status">
          {copied}
        </p>
      )}
      {error && !trashing && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {editing && folders !== null && (
        <EditDialog
          action={editing}
          item={detail}
          tree={tree}
          tags={tags}
          onClose={() => setEditing(null)}
          onClosed={() => buttonRef.current?.focus()}
          onSaved={(message) => {
            setEditing(null);
            onChanged(message);
          }}
          onTagCreated={() => listTags().then((result) => setTags(result.items), () => undefined)}
        />
      )}
      {locking && (
        <LockDialog
          documents={[detail]}
          onClose={() => setLocking(false)}
          onClosed={() => buttonRef.current?.focus()}
          onLocked={(message) => {
            setLocking(false);
            (onLocked ?? onChanged)(message);
          }}
        />
      )}
      {trashing && (
        <ActionDialog
          title="Move to Trash"
          onClose={() => setTrashing(false)}
          onClosed={() => buttonRef.current?.focus()}
          canClose={() => !busy}
        >
          <div className={libraryStyles.dialogForm}>
            <p>{trashQuestion(detail)}</p>
            {error && (
              <p className="error-text" role="alert">
                {error}
              </p>
            )}
            <div className={libraryStyles.formButtons}>
              <button type="button" className={`btn btn-danger ${libraryStyles.apart}`} onClick={trash} disabled={busy}>
                <BusyLabel busy={busy} idle="Move to Trash" working="Moving…" />
              </button>
              <button type="button" className="btn" data-autofocus onClick={() => setTrashing(false)} disabled={busy}>
                Cancel
              </button>
            </div>
          </div>
        </ActionDialog>
      )}
    </>
  );
}
