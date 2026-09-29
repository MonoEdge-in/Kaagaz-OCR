"use client";

// The folder tree: "All documents", then every folder, nested and sorted by
// name (folderTree.ts). Each entry is a link to that folder's view, so it can
// be opened in a new tab and Back returns to the folder before. It is shown
// beside the list from 1024 px and inside a Drawer below that; the documents
// screen renders it in both places.

import Link from "next/link";
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
}

export function FolderNav({ tree, library, loading, error, onRetry, onNavigate }: FolderNavProps) {
  return (
    <div className={styles.treeWrap}>
      <ul className={styles.tree}>
        <li>
          <Link
            href={libraryHref({ ...library, folderId: null, subfolders: false })}
            scroll={false}
            className={styles.treeLink}
            aria-current={library.folderId === null ? "page" : undefined}
            onClick={() => onNavigate("All documents")}
          >
            All documents
          </Link>
        </li>
        {tree.roots.map((node) => (
          <FolderItem key={node.folder.id} node={node} library={library} onNavigate={onNavigate} />
        ))}
        {/* WP1.6: the Trash entry goes here, as the last item of this list. */}
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
}: {
  node: FolderNode;
  library: LibraryState;
  onNavigate(placeName: string): void;
}) {
  const here = library.folderId === node.folder.id;
  return (
    <li>
      <Link
        href={libraryHref({ ...library, folderId: node.folder.id })}
        scroll={false}
        className={styles.treeLink}
        aria-current={here ? "page" : undefined}
        onClick={() => onNavigate(node.folder.name)}
      >
        {node.folder.name}
      </Link>
      {node.children.length > 0 && (
        <ul className={styles.subtree}>
          {node.children.map((child) => (
            <FolderItem key={child.folder.id} node={child} library={library} onNavigate={onNavigate} />
          ))}
        </ul>
      )}
    </li>
  );
}
