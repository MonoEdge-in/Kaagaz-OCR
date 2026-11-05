"use client";

// Upload a folder (#484), the step before anything is sent: what was read (folders, files, size,
// how deep, the hidden and system files skipped), where it goes ("Inside a folder", the one
// being viewed at first, or "As a new department" for an admin), what to do when a folder of
// the same name is already there (add to it, or keep separate as "A (2)"), and what stops it
// (too deep, a name Kaagaz can't keep, over this organisation's limits), with Start disabled.
// The server checks all of it again. While a dropped folder is still being read, it says how
// many files it has found.
// Adding to a folder already there offers "Add new files only", on: each file is checked and
// only the ones Kaagaz doesn't have are sent, so the file and size limits count only those. The
// place of this person's last upload of a folder of the same name is chosen at first.

import { useId, useState } from "react";
import type { FormEvent } from "react";
import { isDepartment } from "@/components/files/departments";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";
import type { FolderUploadLimits, FolderUploadPrevious } from "@/lib/types";
import { ActionDialog } from "./ActionDialog";
import { visibleName } from "./folderPick";
import type { Manifest } from "./folderPick";
import { FolderPicker } from "./FolderPicker";
import { MAX_FOLDER_DEPTH } from "./folderTree";
import type { FolderTree } from "./folderTree";
import type { FolderSource, FolderTarget } from "./folderUpload";
import styles from "./Library.module.css";

const plural = (count: number, one: string, many: string) => `${formatNumber(count)} ${count === 1 ? one : many}`;

/** Why this folder can't be uploaded here, in plain words; null when it can. Adding only new files,
 * the file and size limits count only the files sent, which the server checks once it knows them. */
export function dialogProblem(
  manifest: Manifest,
  targetDepth: number,
  limits: FolderUploadLimits | undefined,
  onlyNew = false,
): string | null {
  const most = limits?.max_depth ?? MAX_FOLDER_DEPTH;
  const deep = targetDepth + manifest.height;
  if (deep > most) {
    return `This folder goes ${deep} levels deep here. Kaagaz keeps at most ${most} levels. Choose a place nearer the top.`;
  }
  if (manifest.badName) {
    const { name, problem } = manifest.badName;
    return problem === "long"
      ? `A folder name is longer than 100 characters: “${name}”. Rename it, then choose the folder again.`
      : `A folder name has a character Kaagaz can’t keep: “${visibleName(name)}”. Rename it, then choose the folder again.`;
  }
  if (!limits) return null;
  if (!onlyNew && manifest.fileCount > limits.max_files) {
    return `A folder upload can hold at most ${formatNumber(limits.max_files)} files. This one has ${formatNumber(manifest.fileCount)}.`;
  }
  if (!onlyNew && manifest.totalBytes > limits.max_bytes) {
    return `A folder upload can be at most ${formatBytes(limits.max_bytes)}. This one is ${formatBytes(manifest.totalBytes)}.`;
  }
  if (manifest.folders.length > limits.max_folders) {
    return `A folder upload can make at most ${formatNumber(limits.max_folders)} folders. This one has ${formatNumber(manifest.folders.length)}.`;
  }
  return null;
}

interface Clash {
  merge: boolean; // false: a plain folder where a department goes, never merged into
  existing: string;
  separate: string; // the name Keep separate gives it
}

/** A folder of the same name (any letter case) where this one would go, or null. */
export function clashOf(tree: FolderTree, target: FolderTarget, rootName: string): Clash | null {
  const siblings =
    target.kind === "department" || target.parentId === null
      ? tree.roots
      : (tree.byId.get(target.parentId)?.children ?? []);
  const taken = new Set(siblings.map((node) => node.folder.name.toLocaleLowerCase("en-IN")));
  const same = siblings.find((node) => node.folder.name.toLocaleLowerCase("en-IN") === rootName.toLocaleLowerCase("en-IN"));
  if (!same) return null;
  let n = 2;
  while (taken.has(`${rootName} (${n})`.toLocaleLowerCase("en-IN"))) n += 1;
  return {
    merge: target.kind === "folder" || isDepartment(same.folder),
    existing: same.folder.name,
    separate: `${rootName} (${n})`,
  };
}

/** What Start sends: add to the folder already there or not, and only new files only when adding to it. */
export function startChoice(clash: Clash | null, merge: boolean, onlyNew: boolean): { merge: boolean; onlyNew: boolean } {
  const adding = clash !== null && clash.merge && merge;
  return { merge: clash === null || adding, onlyNew: adding && onlyNew };
}

/** The dialog's lines about what was read. */
export function summaryLines(source: FolderSource, onlyNew = false): { counts: string; skipped: string | null; read: string } {
  const { manifest, picked } = source;
  return {
    counts: `${plural(manifest.folders.length, "folder", "folders")}, ${plural(manifest.fileCount, "file", "files")}, ${formatBytes(manifest.totalBytes)}. ${plural(manifest.height, "level", "levels")} deep.`,
    skipped: picked.skippedCount === 0 ? null : `${plural(picked.skippedCount, "hidden or system file", "hidden or system files")} skipped`,
    read: onlyNew
      ? `Kaagaz will check ${plural(manifest.fileCount, "file", "files")} and send only the new ones.`
      : `Kaagaz will read ${plural(manifest.fileCount, "file", "files")}. Reading can take a while for big folders.`,
  };
}

const ONLY_NEW_HINT =
  "Kaagaz checks each file on this computer and sends only the ones it doesn’t have. Files already in Kaagaz stay where they are.";

const EMPTY_FOLDERS_NOTE =
  "This browser can’t see empty folders, so they won’t be made. Drag the folder here instead to keep them.";

interface FolderUploadDialogProps {
  source: FolderSource;
  tree: FolderTree;
  folderId: string | null; // the folder being viewed: where it goes at first
  admin: boolean;
  limits: FolderUploadLimits | undefined;
  previous?: FolderUploadPrevious | null; // this person's last upload of a folder of this name: chosen at first
  onStart(target: FolderTarget, merge: boolean, onlyNew: boolean): void;
  onClose(): void;
}

export function FolderUploadDialog({ source, tree, folderId, admin, limits, previous = null, onStart, onClose }: FolderUploadDialogProps) {
  const id = useId();
  const root = source.picked.rootName;
  // The last upload's place, when it can still be chosen here.
  const last =
    previous &&
    (previous.target === "department"
      ? admin
      : previous.parent_folder_id === null || tree.byId.has(previous.parent_folder_id))
      ? previous
      : null;
  const viewed = folderId !== null && tree.byId.has(folderId) ? folderId : null;
  const [where, setWhere] = useState<"folder" | "department">(last?.target ?? "folder");
  const [inside, setInside] = useState<string | null>(last?.target === "folder" ? last.parent_folder_id : viewed);
  const [merge, setMerge] = useState(true);
  const [onlyNew, setOnlyNew] = useState(true);
  const target: FolderTarget = where === "department" ? { kind: "department" } : { kind: "folder", parentId: inside };
  const depth = target.kind === "folder" && inside !== null ? (tree.byId.get(inside)?.depth ?? 0) : 0;
  const clash = clashOf(tree, target, root);
  const choice = startChoice(clash, merge, onlyNew);
  const problem = dialogProblem(source.manifest, depth, limits, choice.onlyNew);
  const lines = summaryLines(source, choice.onlyNew);
  const skippedMore = source.picked.skippedCount - source.picked.skipped.length;
  const lastHere =
    last !== null && (last.target === "department" ? where === "department" : where === "folder" && inside === last.parent_folder_id);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (problem === null) onStart(target, choice.merge, choice.onlyNew);
  }

  return (
    <ActionDialog title={`Upload “${root}”`} onClose={onClose}>
      <form className={styles.dialogForm} onSubmit={submit} noValidate>
        <p>{lines.counts}</p>
        <p className="hint">Kaagaz keeps its folders: every file goes into the same folder it is in now.</p>
        {lines.skipped && (
          <details>
            <summary>{lines.skipped}</summary>
            <ul className={styles.folderChoices}>
              {source.picked.skipped.map((path) => (
                <li key={path} className="hint">
                  {path}
                </li>
              ))}
              {skippedMore > 0 && <li className="hint">and {formatNumber(skippedMore)} more</li>}
            </ul>
          </details>
        )}
        {!source.picked.seesEmptyFolders && <p className="hint">{EMPTY_FOLDERS_NOTE}</p>}

        {admin && (
          <fieldset className={styles.fieldset}>
            <legend>Where it goes</legend>
            <ul className={styles.choices}>
              <li>
                <label className={styles.check}>
                  <input type="radio" name={`${id}-where`} checked={where === "folder"} onChange={() => setWhere("folder")} />
                  Inside a folder
                </label>
              </li>
              <li>
                <label className={styles.check}>
                  <input
                    type="radio"
                    name={`${id}-where`}
                    checked={where === "department"}
                    onChange={() => setWhere("department")}
                  />
                  As a new department
                </label>
              </li>
            </ul>
          </fieldset>
        )}

        {where === "folder" && (
          <fieldset className={styles.fieldset}>
            <legend>Put “{root}” inside</legend>
            <ul className={styles.folderChoices}>
              <li>
                <label className={styles.check}>
                  <input type="radio" name={`${id}-inside`} checked={inside === null} onChange={() => setInside(null)} />
                  All documents (top level)
                </label>
              </li>
              <FolderPicker tree={tree} name={`${id}-inside`} choice={inside} onChoose={setInside} />
            </ul>
          </fieldset>
        )}

        {last && lastHere && (
          <p className="hint">
            You uploaded “{root}” here on {formatDate(last.created_at)}.
          </p>
        )}

        {clash && clash.merge && (
          <fieldset className={styles.fieldset}>
            <legend>A folder named “{clash.existing}” is already there</legend>
            <ul className={styles.folderChoices}>
              <li>
                <label className={styles.check}>
                  <input type="radio" name={`${id}-clash`} checked={merge} onChange={() => setMerge(true)} />
                  Add to the existing “{clash.existing}”
                </label>
              </li>
              <li>
                <label className={styles.check}>
                  <input type="radio" name={`${id}-clash`} checked={!merge} onChange={() => setMerge(false)} />
                  Keep separate (makes “{clash.separate}”)
                </label>
              </li>
            </ul>
          </fieldset>
        )}
        {clash && clash.merge && merge && (
          <div>
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={onlyNew}
                aria-describedby={`${id}-only-new`}
                onChange={(event) => setOnlyNew(event.target.checked)}
              />
              Add new files only
            </label>
            <p id={`${id}-only-new`} className="hint">
              {ONLY_NEW_HINT}
            </p>
          </div>
        )}
        {clash && !clash.merge && (
          <p className="hint">
            A folder named “{clash.existing}” is already at the top level, so the department will be “{clash.separate}”.
          </p>
        )}

        {problem ? (
          <p className="error-text" role="alert">
            {problem}
          </p>
        ) : (
          <p>{lines.read}</p>
        )}
        <div className={styles.formButtons}>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={problem !== null}>
            Start upload
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}

/** While a dropped folder is read: how many files so far, and Cancel. */
export function FolderReadingDialog({
  name,
  found,
  failed = false,
  onClose,
}: {
  name: string;
  found: number;
  failed?: boolean | string; // the browser couldn't read it (moved, or no permission), or these words
  onClose(): void;
}) {
  return (
    <ActionDialog title={name ? `Upload “${name}”` : "Upload a folder"} onClose={onClose}>
      <div className={styles.dialogForm}>
        {failed ? (
          <p className="error-text" role="alert">
            {failed === true ? "Kaagaz couldn’t read this folder. Try again, or choose it with Upload a folder." : failed}
          </p>
        ) : (
          <>
            <p role="status">Reading the folder…</p>
            <p className="hint">{plural(found, "file", "files")} found so far.</p>
          </>
        )}
        <div className={styles.formButtons}>
          <button type="button" className="btn" onClick={onClose}>
            {failed ? "Close" : "Cancel"}
          </button>
        </div>
      </div>
    </ActionDialog>
  );
}
