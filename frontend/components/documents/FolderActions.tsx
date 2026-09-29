"use client";

// What can be done with the folder being viewed: New folder (inside it, or at
// the top level from All documents), Rename, Move and Delete. Each opens a
// small form in place; errors stay next to it. The server has the last word:
// a taken name, a move into itself, more than 5 levels or a folder that is not
// empty come back as 409 and are shown as it words them. The screen gives this
// a new key for each folder, so an open form never outlives its folder.

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { FormEvent, MouseEvent } from "react";
import { Icon } from "@/components/ui/Icon";
import { createFolder, deleteFolder, updateFolder } from "@/lib/api";
import { MAX_FOLDER_DEPTH, canAddSubfolder, pathLabel, validMoveTargets } from "./folderTree";
import type { FolderNode, FolderTree } from "./folderTree";
import { folderNameProblem, libraryErrorText } from "./library";
import styles from "./Library.module.css";

type Mode = "new" | "rename" | "move" | "delete";

export interface FolderActionsProps {
  tree: FolderTree;
  current: FolderNode | null; // null: All documents
  /** A change was saved. `goTo` is the folder to show next (null: all documents), when it changes. */
  onChanged(message: string, goTo?: { folderId: string | null }): void;
}

export function FolderActions({ tree, current, onChanged }: FolderActionsProps) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [name, setName] = useState("");
  const [target, setTarget] = useState(""); // "" is the top level
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const opener = useRef<HTMLButtonElement | null>(null);
  const formId = useId();
  const fieldId = `${formId}-field`;
  const errorId = `${formId}-error`;

  const focusOnMount = useCallback((element: HTMLElement | null) => {
    element?.focus();
  }, []);

  // Closing the form sends focus back to the button that opened it.
  useEffect(() => {
    if (mode === null && opener.current?.isConnected) {
      opener.current.focus();
      opener.current = null;
    }
  }, [mode]);

  const parentId = current?.parent?.folder.id ?? "";

  function open(next: Mode, event: MouseEvent<HTMLButtonElement>) {
    opener.current = event.currentTarget;
    setMode(next);
    setError(null);
    setName(next === "rename" && current ? current.folder.name : "");
    setTarget(parentId);
  }

  function close() {
    setMode(null);
    setError(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mode === "new" || mode === "rename") {
      const problem = folderNameProblem(name);
      if (problem) {
        setError(problem);
        return;
      }
    }
    setBusy(true);
    setError(null);
    try {
      if (mode === "new") {
        const created = await createFolder(name.trim(), current?.folder.id ?? null);
        onChanged(`Created the folder ${created.name}.`);
      } else if (mode === "rename" && current) {
        const renamed = await updateFolder(current.folder.id, { name: name.trim() });
        onChanged(`Renamed the folder to ${renamed.name}.`);
      } else if (mode === "move" && current) {
        const moved = await updateFolder(current.folder.id, { parent_id: target || null });
        const where = target ? pathLabel(tree, target) : "the top level";
        onChanged(`Moved the folder ${moved.name} to ${where}.`);
      } else if (mode === "delete" && current) {
        await deleteFolder(current.folder.id);
        opener.current = null; // the screen shows the parent and focuses its heading
        onChanged(`Deleted the folder ${current.folder.name}.`, {
          folderId: current.parent?.folder.id ?? null,
        });
      }
      setMode(null);
    } catch (caught) {
      setError(libraryErrorText(caught, "folder"));
    } finally {
      setBusy(false);
    }
  }

  const here = current ? current.folder.name : null;
  const fits = canAddSubfolder(tree, current?.folder.id ?? null);
  const targets = mode === "move" && current ? validMoveTargets(tree, current.folder.id) : [];
  const invalid = error ? { "aria-invalid": true, "aria-describedby": errorId } : {};

  let body = null;
  if (mode === "new" && !fits) {
    body = (
      <p>
        Folders can be at most {MAX_FOLDER_DEPTH} levels deep, so “{here}” can’t hold another one.
      </p>
    );
  } else if (mode === "new" || mode === "rename") {
    body = (
      <div className="field">
        <label htmlFor={fieldId}>
          {mode === "rename"
            ? `New name for “${here}”`
            : here
              ? `Name of the new folder in “${here}”`
              : "Name of the new folder"}
        </label>
        <input
          ref={focusOnMount}
          id={fieldId}
          className="input"
          value={name}
          maxLength={100}
          autoComplete="off"
          onChange={(event) => setName(event.target.value)}
          {...invalid}
        />
      </div>
    );
  } else if (mode === "move") {
    body = (
      <div className="field">
        <label htmlFor={fieldId}>Move “{here}” to</label>
        <select
          ref={focusOnMount}
          id={fieldId}
          className="input"
          value={target}
          onChange={(event) => setTarget(event.target.value)}
          {...invalid}
        >
          <option value="">Top level</option>
          {targets.map((node) => (
            <option key={node.folder.id} value={node.folder.id}>
              {pathLabel(tree, node.folder.id)}
            </option>
          ))}
        </select>
        <p className="hint">Only places that keep folders within {MAX_FOLDER_DEPTH} levels are listed.</p>
      </div>
    );
  } else if (mode === "delete") {
    body = <p>Delete the folder “{here}”? Only an empty folder can be deleted.</p>;
  }

  const submitWords: Record<Mode, string> = {
    new: "Create folder",
    rename: "Save name",
    move: "Move folder",
    delete: "Delete folder",
  };
  const blocked = mode === "new" && !fits;
  const unchanged = mode === "move" && target === parentId;

  return (
    <div className={styles.folderActions}>
      <div className={styles.folderButtons}>
        <button
          type="button"
          className="btn"
          aria-expanded={mode === "new"}
          onClick={(event) => open("new", event)}
        >
          <Icon name="plus" />
          New folder
        </button>
        {current && (
          <>
            <button
              type="button"
              className="btn btn-quiet"
              aria-expanded={mode === "rename"}
              onClick={(event) => open("rename", event)}
            >
              Rename<span className="visually-hidden"> this folder</span>
            </button>
            <button
              type="button"
              className="btn btn-quiet"
              aria-expanded={mode === "move"}
              onClick={(event) => open("move", event)}
            >
              Move<span className="visually-hidden"> this folder</span>
            </button>
            <button
              type="button"
              className={`btn btn-quiet ${styles.danger}`}
              aria-expanded={mode === "delete"}
              onClick={(event) => open("delete", event)}
            >
              <Icon name="trash" />
              Delete<span className="visually-hidden"> this folder</span>
            </button>
          </>
        )}
      </div>

      {mode !== null && (
        <form className={styles.folderForm} onSubmit={submit} noValidate aria-label={submitWords[mode]}>
          {body}
          {error && (
            <p id={errorId} className="error-text" role="alert">
              {error}
            </p>
          )}
          <div className={styles.formButtons}>
            {!blocked && (
              <button
                type="submit"
                className={mode === "delete" ? "btn btn-danger" : "btn btn-primary"}
                disabled={busy || unchanged}
              >
                {busy && <span className="spinner" aria-hidden="true" />}
                {submitWords[mode]}
              </button>
            )}
            <button
              ref={mode === "delete" || blocked ? focusOnMount : undefined}
              type="button"
              className="btn"
              onClick={close}
              disabled={busy}
            >
              {blocked ? "Close" : "Cancel"}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
