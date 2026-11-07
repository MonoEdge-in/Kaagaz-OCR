"use client";

// An admin deletes a folder that holds files (#537, Tushar, 7 Nov 2026). Two choices: move what
// is inside to a folder they pick (the parent first; never the folder itself or one inside it,
// FolderPicker with validMoveTargets) and delete only the folder, or send everything inside to
// Trash, which needs the folder's name typed exactly. The UI kit's ConfirmDialog gives the modal,
// its focus trap, Esc and Cancel first; the server's refusal (an upload still running) shows in it.

import { useId, useState } from "react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { deleteFolderWithContents } from "@/lib/api";
import type { FolderSummary } from "@/lib/types";
import { RESTORE_DAYS, heldLine, keptMessage, trashButton, trashedMessage } from "./folderDelete";
import type { FolderNoun } from "./folderDelete";
import { pathLabel, validMoveTargets } from "./folderTree";
import type { FolderNode, FolderTree } from "./folderTree";
import { FolderPicker } from "./FolderPicker";
import { libraryErrorText } from "./library";
import styles from "./Library.module.css";

export interface DeleteFolderDialogProps {
  tree: FolderTree;
  node: FolderNode;
  summary: FolderSummary;
  /** Deleted: the toast's words. */
  onDeleted(message: string): void;
  onCancel(): void;
}

export function DeleteFolderDialog({ tree, node, summary, onDeleted, onCancel }: DeleteFolderDialogProps) {
  const name = node.folder.name;
  const noun: FolderNoun = node.folder.kind === "department" ? "department" : "folder";
  const [choice, setChoice] = useState<"keep" | "trash">("keep");
  const [to, setTo] = useState(node.parent?.folder.id ?? ""); // "" is the top level
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [targets] = useState(() => new Set(validMoveTargets(tree, node.folder.id, true).map((each) => each.folder.id)));
  // A name typed on Windows is NFC; one a Mac made may be stored decomposed.
  const typedRight = typed.normalize("NFC") === name.normalize("NFC");
  const formId = useId();
  const confirmId = `${formId}-confirm`;

  async function confirm() {
    setBusy(true);
    setError(null);
    let message: string;
    try {
      if (choice === "keep") {
        const moved = await deleteFolderWithContents(node.folder.id, { contents: "keep", to: to || null });
        message = keptMessage(noun, to ? pathLabel(tree, to) : null, moved.files);
      } else {
        const trashed = await deleteFolderWithContents(node.folder.id, { contents: "trash" });
        message = trashedMessage(noun, trashed.files);
      }
    } catch (caught) {
      setError(libraryErrorText(caught, noun));
      setBusy(false);
      return;
    }
    onDeleted(message); // outside the try: the screen's own work after it is not a failed delete
  }

  return (
    <ConfirmDialog
      open
      title={`Delete “${name}”?`}
      confirmLabel={choice === "keep" ? `Move and delete ${noun}` : trashButton(summary.files, noun)}
      busy={busy}
      confirmDisabled={choice === "trash" && !typedRight}
      error={error}
      onConfirm={confirm}
      onCancel={onCancel}
    >
      <p>{heldLine(summary)}</p>
      <fieldset className={styles.fieldset}>
        <legend className="visually-hidden">What happens to what is inside</legend>
        <ul className={styles.folderChoices}>
          <li>
            <label className={styles.check}>
              <input type="radio" name={formId} checked={choice === "keep"} disabled={busy} onChange={() => setChoice("keep")} />
              Move what’s inside, then delete the {noun}
            </label>
          </li>
          {choice === "keep" && (
            <li>
              <fieldset className={styles.fieldset}>
                <legend>Move to</legend>
                <ul className={styles.folderChoices}>
                  <li>
                    <label className={styles.check}>
                      <input type="radio" name={`${formId}-to`} value="" checked={to === ""} disabled={busy} onChange={() => setTo("")} />
                      Top level
                    </label>
                  </li>
                  <FolderPicker
                    tree={tree}
                    name={`${formId}-to`}
                    choice={to || null}
                    onChoose={setTo}
                    disabled={(each) => !targets.has(each.folder.id)}
                    busy={busy}
                  />
                </ul>
              </fieldset>
            </li>
          )}
          <li>
            <label className={styles.check}>
              <input type="radio" name={formId} checked={choice === "trash"} disabled={busy} onChange={() => setChoice("trash")} />
              Delete the {noun} and everything inside
            </label>
          </li>
          {choice === "trash" && (
            <li>
              <p className="hint">Everything inside goes to Trash. You can restore it for {RESTORE_DAYS} days.</p>
              <div className="field">
                <label htmlFor={confirmId}>Type {name} to confirm</label>
                <input
                  id={confirmId}
                  className="input"
                  value={typed}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={busy}
                  onChange={(event) => setTyped(event.target.value)}
                />
              </div>
            </li>
          )}
        </ul>
      </fieldset>
    </ConfirmDialog>
  );
}
