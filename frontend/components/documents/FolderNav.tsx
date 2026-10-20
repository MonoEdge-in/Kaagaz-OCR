"use client";

// The folder tree: "All documents", then every folder, nested and sorted by
// name (folderTree.ts). Each entry is a link to that folder's view, so it can
// be opened in a new tab and Back returns to the folder before. The filters in
// use stay as they are. Trash comes last, set apart from the folders. It is
// shown beside the list from 1024 px and inside a Drawer below that; the
// documents screen renders it in both places. UI audit D12 (16 Oct 2026): a document's
// name dragged from the list onto a folder (or All documents: the top level) moves it
// there (`onDropDocument`); the folder under it is marked while it hovers.

import Link from "next/link";
import { useState } from "react";
import type { DragEvent } from "react";
import { departmentsOf, isDepartment } from "@/components/files/departments";
import { FILES_TEXT } from "@/components/files/words";
import { Icon } from "@/components/ui/Icon";
import type { FolderNode, FolderTree } from "./folderTree";
import { libraryHref } from "./libraryQuery";
import type { LibraryState } from "./libraryQuery";
import styles from "./Library.module.css";

export interface FolderNavProps {
  tree: FolderTree;
  library: LibraryState;
  loading: boolean;
  error: string | null;
  onRetry(): void;
  /** A folder was picked: the screen announces it and closes the drawer. */
  onNavigate(placeName: string): void;
  /** A document dropped on a folder (null: the top level). */
  onDropDocument?(documentId: string, folderId: string | null, placeName: string): void;
}

/** What a document's row carries while it is dragged (DocumentsTable.tsx). */
export const DOCUMENT_DRAG = "application/x-kaagaz-document";

/** The drop handlers for one folder's link; `over` marks it while a document hovers. */
function useDropTarget(folderId: string | null, placeName: string, onDrop: FolderNavProps["onDropDocument"]) {
  const [over, setOver] = useState(false);
  if (!onDrop) return { over: false, handlers: {} };
  const carries = (event: DragEvent) => event.dataTransfer.types.includes(DOCUMENT_DRAG);
  return {
    over,
    handlers: {
      onDragOver: (event: DragEvent) => {
        if (!carries(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        setOver(true);
      },
      onDragLeave: () => setOver(false),
      onDrop: (event: DragEvent) => {
        if (!carries(event)) return;
        event.preventDefault();
        setOver(false);
        onDrop(event.dataTransfer.getData(DOCUMENT_DRAG), folderId, placeName);
      },
    },
  };
}

export function FolderNav({ tree, library, loading, error, onRetry, onNavigate, onDropDocument }: FolderNavProps) {
  const top = useDropTarget(null, "the top level", onDropDocument);
  // File management F5: the departments first, in the admin's order, then Other: the
  // folders in no department, and Trash.
  const order = departmentsOf(tree.roots.map((node) => node.folder)).map((folder) => folder.id);
  const departments = order.flatMap((id) => tree.roots.filter((node) => node.folder.id === id));
  const others = tree.roots.filter((node) => !isDepartment(node.folder));
  const item = (node: FolderNode) => (
    <FolderItem key={node.folder.id} node={node} library={library} onNavigate={onNavigate} onDrop={onDropDocument} />
  );
  return (
    <div className={styles.treeWrap}>
      <ul className={styles.tree}>
        <li>
          <Link
            href={libraryHref({ ...library, folderId: null, subfolders: false, trash: false })}
            scroll={false}
            className={styles.treeLink}
            aria-current={library.folderId === null && !library.trash ? "page" : undefined}
            data-drop={top.over ? "true" : undefined}
            onClick={() => onNavigate("All documents")}
            {...top.handlers}
          >
            All documents
          </Link>
        </li>
        {departments.length > 0 && (
          <li className={styles.treeGroup}>
            <span id="tree-departments" className={styles.groupHeading}>
              {FILES_TEXT.departments}
            </span>
            <ul className={styles.tree} aria-labelledby="tree-departments">
              {departments.map(item)}
            </ul>
          </li>
        )}
        {departments.length === 0 ? (
          others.map(item)
        ) : (
          <li className={styles.treeGroup}>
            <span id="tree-other" className={styles.groupHeading}>
              {FILES_TEXT.other}
            </span>
            {others.length > 0 && (
              <>
                <span id="tree-outside" className={styles.groupSub}>
                  {FILES_TEXT.notInDepartment}
                </span>
                <ul className={styles.tree} aria-labelledby="tree-outside">
                  {others.map(item)}
                </ul>
              </>
            )}
          </li>
        )}
        <li className={styles.trashItem}>
          <Link
            href={libraryHref({ ...library, folderId: null, subfolders: false, trash: true })}
            scroll={false}
            className={styles.treeLink}
            aria-current={library.trash ? "page" : undefined}
            onClick={() => onNavigate("Trash")}
          >
            <Icon name="trash" />
            Trash
          </Link>
        </li>
      </ul>
      {loading && (
        <p className={`muted delayed ${styles.note} ${styles.treeNote}`} role="status">
          Loading folders…
        </p>
      )}
      {error && (
        <div className={`${styles.note} ${styles.treeNote}`}>
          <p className="error-text" role="alert">
            Could not load the folders. {error}
          </p>
          <button type="button" className="btn" onClick={onRetry}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
    </div>
  );
}

function FolderItem({
  node,
  library,
  onNavigate,
  onDrop,
}: {
  node: FolderNode;
  library: LibraryState;
  onNavigate(placeName: string): void;
  onDrop: FolderNavProps["onDropDocument"];
}) {
  const here = library.folderId === node.folder.id;
  const target = useDropTarget(node.folder.id, node.folder.name, onDrop);
  return (
    <li>
      <Link
        href={libraryHref({ ...library, folderId: node.folder.id, trash: false })}
        scroll={false}
        className={styles.treeLink}
        aria-current={here ? "page" : undefined}
        data-drop={target.over ? "true" : undefined}
        onClick={() => onNavigate(node.folder.name)}
        {...target.handlers}
      >
        {node.folder.name}
      </Link>
      {node.children.length > 0 && (
        <ul className={styles.subtree}>
          {node.children.map((child) => (
            <FolderItem key={child.folder.id} node={child} library={library} onNavigate={onNavigate} onDrop={onDrop} />
          ))}
        </ul>
      )}
    </li>
  );
}
