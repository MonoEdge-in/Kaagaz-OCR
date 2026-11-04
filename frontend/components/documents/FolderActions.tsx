"use client";

// What can be done with the folder being viewed: New folder (inside it, or at
// the top level from All documents), Rename, Move and Delete. Each opens a
// small form in place; errors stay next to it. The server has the last word:
// a taken name, a move into itself, more than 100 levels or a folder that is not
// empty come back as 409 and are shown as it words them. The screen gives this
// a new key for each folder, so an open form never outlives its folder.
// UI audit D12 (16 Oct 2026): the folder column header's "+" is the one New folder (Tushar, 16 Oct
// 2026: no second button here); it opens the New folder form here (`askNew` counts its presses).
// Suggested names (Tushar, 16 Oct 2026): under the New folder name, up to 4 one-press names from
// the kinds of documents listed that have no folder yet (folderNameSuggestions, library.ts).

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { FormEvent, MouseEvent } from "react";
import { Icon } from "@/components/ui/Icon";
import { createFolder, deleteFolder, updateFolder } from "@/lib/api";
import { BusyLabel } from "./BusyLabel";
import { MAX_FOLDER_DEPTH, canAddSubfolder, pathLabel, validMoveTargets } from "./folderTree";
import type { FolderNode, FolderTree } from "./folderTree";
import { FolderPicker } from "./FolderPicker";
import { folderNameProblem, libraryErrorText } from "./library";
import styles from "./Library.module.css";

type Mode = "new" | "rename" | "move" | "delete";

export interface FolderActionsProps {
  tree: FolderTree;
  current: FolderNode | null; // null: All documents
  /** A change was saved. `goTo` is the folder to show next (null: all documents), when it changes. */
  onChanged(message: string, goTo?: { folderId: string | null }): void;
  askNew?: number;
  /** Names to offer for a new folder (folderNameSuggestions). */
  suggestions?: readonly string[];
}

/** The suggested names as square chips; pressing one puts it in the name box. */
export function NameChips({ names, picked, onPick }: { names: readonly string[]; picked: string; onPick(name: string): void }) {
  const labelId = useId();
  if (names.length === 0) return null;
  return (
    <div className={styles.nameChips} role="group" aria-labelledby={labelId}>
      <span id={labelId} className="hint">
        Suggested names
      </span>
      {names.map((name) => (
        <button
          key={name}
          type="button"
          className={styles.chip}
          data-active={picked.trim() === name || undefined}
          aria-pressed={picked.trim() === name}
          onClick={() => onPick(name)}
        >
          {name}
        </button>
      ))}
    </div>
  );
}

export function FolderActions({ tree, current, onChanged, askNew = 0, suggestions = [] }: FolderActionsProps) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [heardNew, setHeardNew] = useState(askNew);
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
  if (askNew !== heardNew) {
    setHeardNew(askNew);
    setMode("new");
    setError(null);
    setName("");
    setTarget(parentId);
  }

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
  const targets = new Set(mode === "move" && current ? validMoveTargets(tree, current.folder.id).map((node) => node.folder.id) : []);
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
        {mode === "new" && <NameChips names={suggestions} picked={name} onPick={setName} />}
      </div>
    );
  } else if (mode === "move") {
    body = (
      // #484: a tree that opens one folder at a time (FolderPicker), never one list of every folder.
      <fieldset className={styles.fieldset} {...invalid}>
        <legend>Move “{here}” to</legend>
        <ul className={styles.folderChoices}>
          <li>
            <label className={styles.check}>
              <input
                ref={focusOnMount}
                type="radio"
                name={fieldId}
                value=""
                checked={target === ""}
                onChange={() => setTarget("")}
              />
              Top level
            </label>
          </li>
          <FolderPicker
            tree={tree}
            name={fieldId}
            choice={target || null}
            onChoose={setTarget}
            disabled={(node) => !targets.has(node.folder.id)}
          />
        </ul>
        <p className="hint">Places that would make folders deeper than {MAX_FOLDER_DEPTH} levels can’t be chosen.</p>
      </fieldset>
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
  const submitButton = mode !== null && (
    <button
      type="submit"
      className={mode === "delete" ? `btn btn-danger ${styles.apart}` : "btn btn-primary"}
      disabled={busy || unchanged}
    >
      <BusyLabel busy={busy} idle={submitWords[mode]} working={submitWords[mode]} />
    </button>
  );

  return (
    <div className={styles.folderActions}>
      {current && (
        <div className={styles.folderButtons}>
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
        </div>
      )}

      {mode !== null && (
        <form className={styles.folderForm} onSubmit={submit} noValidate aria-label={submitWords[mode]}>
          {body}
          {error && (
            <p id={errorId} className="error-text" role="alert">
              {error}
            </p>
          )}
          <div className={styles.formButtons}>
            {/* [Delete folder] ... [Cancel], or [Cancel] [Primary]. */}
            {!blocked && mode === "delete" && submitButton}
            <button
              ref={mode === "delete" || blocked ? focusOnMount : undefined}
              type="button"
              className="btn"
              onClick={close}
              disabled={busy}
            >
              {blocked ? "Close" : "Cancel"}
            </button>
            {!blocked && mode !== "delete" && submitButton}
          </div>
        </form>
      )}
    </div>
  );
}
