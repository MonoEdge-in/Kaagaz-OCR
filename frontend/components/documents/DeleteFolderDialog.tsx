"use client";

// An admin deletes a folder that holds files (#537, Tushar, 7 Nov 2026). Two choices: move what
// is inside to a folder they pick (the parent first; never the folder itself or one inside it,
// FolderPicker with validMoveTargets) and delete only the folder, or send everything inside to
// Trash, which needs the folder's name typed exactly. The UI kit's ConfirmDialog gives the modal,
// its focus trap, Esc and Cancel first; the server's refusal (an upload still running) shows in it.
// PRD 7 PR 11b (#604, PR 10c): a move of what is inside that changes who can see it is a warning
// first (409 access_would_change, in the server's words): its button becomes "Move anyway and
// delete", which sends it again confirmed; one that opens it to the admin asks their password
// (403 password_required). Choosing another place, or Trash, takes the warning back.

import { useId, useState } from "react";
import { usePasswordStep } from "@/components/access/SelfPasswordDialog";
import { MOVE_ANYWAY, quoted } from "@/components/access/words";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ApiError, deleteFolderWithContents } from "@/lib/api";
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
  const [warned, setWarned] = useState(false); // 409 access_would_change came: the next press confirms
  const passwordStep = usePasswordStep();
  // Another place, or Trash, takes the warning (and what it said) back.
  const choose = (next: "keep" | "trash") => {
    setChoice(next);
    setWarned(false);
    setError(null);
  };
  const place = (next: string) => {
    setTo(next);
    setWarned(false);
    setError(null);
  };
  const [targets] = useState(() => new Set(validMoveTargets(tree, node.folder.id, true).map((each) => each.folder.id)));
  // A name typed on Windows is NFC; one a Mac made may be stored decomposed.
  const typedRight = typed.normalize("NFC") === name.normalize("NFC");
  const formId = useId();
  const confirmId = `${formId}-confirm`;

  async function confirm() {
    setBusy(true);
    if (!warned) setError(null); // the warning stays in view while it is confirmed (and if that is cancelled)
    let message: string;
    try {
      if (choice === "keep") {
        const keep = (password?: string) =>
          deleteFolderWithContents(node.folder.id, { contents: "keep", to: to || null, confirm: warned, password });
        const moved = warned ? await passwordStep.run(quoted(name), keep, false) : await keep();
        if (moved === null) {
          setBusy(false); // the password step was cancelled: nothing more is sent, nothing deleted
          return;
        }
        message = keptMessage(noun, to ? pathLabel(tree, to) : null, moved.files);
      } else {
        const trashed = await deleteFolderWithContents(node.folder.id, { contents: "trash" });
        message = trashedMessage(noun, trashed.files);
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409 && caught.code === "access_would_change") setWarned(true);
      setError(libraryErrorText(caught, noun));
      setBusy(false);
      return;
    }
    onDeleted(message); // outside the try: the screen's own work after it is not a failed delete
  }

  return (
    <>
    <ConfirmDialog
      open
      title={`Delete “${name}”?`}
      confirmLabel={choice === "keep" ? (warned ? MOVE_ANYWAY : `Move and delete ${noun}`) : trashButton(summary.files, noun)}
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
              <input type="radio" name={formId} checked={choice === "keep"} disabled={busy} onChange={() => choose("keep")} />
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
                      <input type="radio" name={`${formId}-to`} value="" checked={to === ""} disabled={busy} onChange={() => place("")} />
                      Top level
                    </label>
                  </li>
                  <FolderPicker
                    tree={tree}
                    name={`${formId}-to`}
                    choice={to || null}
                    onChoose={place}
                    disabled={(each) => !targets.has(each.folder.id)}
                    busy={busy}
                  />
                </ul>
              </fieldset>
            </li>
          )}
          <li>
            <label className={styles.check}>
              <input type="radio" name={formId} checked={choice === "trash"} disabled={busy} onChange={() => choose("trash")} />
              Delete the {noun} and everything inside
            </label>
          </li>
          {choice === "trash" && (
            <li>
              <p className="hint">Everything goes to Trash. You can restore the {noun} for {RESTORE_DAYS} days.</p>
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
    {/* Beside the dialog, not in it: Esc in "Confirm it's you" closes only the step. */}
    {passwordStep.dialog}
    </>
  );
}
