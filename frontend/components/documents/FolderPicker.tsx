"use client";

// The folders to choose from, as radios in a tree that opens one folder at a time, for the
// Move dialogs and Upload a folder (#484: up to 100 levels and thousands of folders). Like
// the Documents tree (FolderNav.tsx) it draws only what is open (visibleNodes), at most
// CHILD_CAP of one folder's folders with "Show all", and the indent stops after 8 levels,
// where each row says its level in words. The way to the chosen folder starts open. Past
// CHILD_CAP folders in all, "Find a folder" lists up to 50 by name, each with its path.
// The caller draws its own other choices (All documents, New folder…) in the same radio group.

import { useId, useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "@/components/ui/Icon/Icon";
import { formatNumber } from "@/lib/format";
import { CHILD_CAP, TOP_LIST, folderPath, pathLabel, searchFolders, shortPathLabel, visibleNodes } from "./folderTree";
import type { FolderNode, FolderTree } from "./folderTree";
import { INDENT_LEVELS, indent } from "./FolderNav";
import styles from "./Library.module.css";

export interface FolderPickerProps {
  tree: FolderTree;
  /** The radio group's name, shared with the caller's own choices. */
  name: string;
  choice: string | null;
  onChoose(id: string): void;
  /** A place that can't be chosen (where the documents are now, or too deep). */
  disabled?(node: FolderNode): boolean;
  /** Words after a folder's name ("Current", "Suggested"). */
  note?(node: FolderNode): ReactNode;
  /** These folders start open, with the way to them, so the one chosen shows. */
  openTo?: readonly (string | null)[];
  busy?: boolean;
  /** The folder that takes the focus when the dialog opens. */
  focusId?: string | null;
}

export function FolderPicker({ tree, name, choice, onChoose, disabled, note, openTo = [choice], busy = false, focusId }: FolderPickerProps) {
  const searchId = useId();
  const [words, setWords] = useState("");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => {
    const open = new Set<string>();
    for (const id of openTo) {
      for (let node = id === null ? undefined : tree.byId.get(id); node; node = node.parent ?? undefined) {
        open.add(node.folder.id);
      }
    }
    return open;
  });
  const [showAll, setShowAll] = useState<ReadonlySet<string>>(new Set());

  const radio = (node: FolderNode, label: ReactNode, title?: string) => (
    <label className={styles.check} title={title}>
      <input
        type="radio"
        name={name}
        value={node.folder.id}
        checked={choice === node.folder.id}
        disabled={busy || disabled?.(node) === true}
        data-autofocus={node.folder.id === focusId ? true : undefined}
        onChange={() => onChoose(node.folder.id)}
      />
      {label}
      {note?.(node)}
    </label>
  );

  const found = words.trim() === "" ? null : searchFolders(tree, words);
  return (
    <>
      {tree.byId.size > CHILD_CAP && (
        <li className={styles.pickerFind}>
          <label htmlFor={searchId} className="visually-hidden">
            Find a folder
          </label>
          <input
            id={searchId}
            className="input"
            type="search"
            placeholder="Find a folder"
            value={words}
            autoComplete="off"
            onChange={(event) => setWords(event.target.value)}
          />
        </li>
      )}
      {found !== null && found.length === 0 && <li className="muted">No folder has that name.</li>}
      {found?.map((node) => (
        <li key={node.folder.id}>{radio(node, shortPathLabel(tree, node.folder.id), pathLabel(tree, node.folder.id))}</li>
      ))}
      {found === null &&
        visibleNodes(tree.roots, expanded, null, showAll).map((row) => {
          if (row.kind === "more") {
            const key = row.parent?.folder.id ?? TOP_LIST;
            return (
              <li key={`more-${key}`} className={styles.treeRow} style={indent(row.depth)}>
                <span className={styles.treeToggle} aria-hidden="true" />
                <button type="button" className={`btn btn-quiet ${styles.treeMore}`} onClick={() => setShowAll((before) => new Set(before).add(key))}>
                  {row.parent
                    ? `Show all ${formatNumber(row.parent.children.length)} folders in ${row.parent.folder.name}`
                    : `Show all ${formatNumber(row.hidden + CHILD_CAP)} folders`}
                </button>
              </li>
            );
          }
          const { node, open, hasChildren } = row;
          const toggle = () =>
            setExpanded((before) => {
              const next = new Set(before);
              if (open) next.delete(node.folder.id);
              else next.add(node.folder.id);
              return next;
            });
          return (
            <li key={node.folder.id} className={styles.treeRow} style={indent(node.depth)} aria-level={node.depth}>
              {hasChildren ? (
                <button
                  type="button"
                  className={styles.treeToggle}
                  aria-expanded={open}
                  aria-label={`${open ? "Hide" : "Show"} folders in ${node.folder.name}`}
                  onClick={toggle}
                >
                  <Icon name={open ? "chev-down" : "chev-right"} size="sm" />
                </button>
              ) : (
                <span className={styles.treeToggle} aria-hidden="true" />
              )}
              {radio(
                node,
                <>
                  {node.folder.name}
                  {node.depth > INDENT_LEVELS && <span className={styles.treeLevel}>Level {node.depth}</span>}
                </>,
                node.depth > INDENT_LEVELS ? folderPath(tree, node.folder.id).map((folder) => folder.name).join(" / ") : undefined,
              )}
            </li>
          );
        })}
    </>
  );
}
