"use client";

// The folder tree: "All documents", then every folder, nested and sorted by
// name (folderTree.ts). Each entry is a link to that folder's view, so it can
// be opened in a new tab and Back returns to the folder before. The filters in
// use stay as they are. Trash comes last, set apart from the folders. It is
// shown beside the list from 1024 px and inside a Drawer below that; the
// documents screen renders it in both places. UI audit D12 (16 Oct 2026): a document's
// name dragged from the list onto a folder (or All documents: the top level) moves it
// there (`onDropDocument`); the folder under it is marked while it hovers.
// Deep trees (#484, folders up to 100 levels): each list is drawn flat (visibleNodes), only
// the open folders' folders, at most CHILD_CAP of one folder's with "Show all" for the rest.
// A folder opens with its button, and the path to the folder shown is always open. Each row
// says its level (aria-level); the indent stops after 8 levels and the level is then in words.
// Past CHILD_CAP folders in all, "Find a folder" searches them by name.

import Link from "next/link";
import { useId, useState } from "react";
import type { CSSProperties, DragEvent } from "react";
import { departmentsOf, isDepartment } from "@/components/files/departments";
import { FILES_TEXT } from "@/components/files/words";
import { Icon } from "@/components/ui/Icon";
import { formatNumber } from "@/lib/format";
import { Icon as TreeIcon } from "@/components/ui/Icon/Icon";
import { CHILD_CAP, TOP_LIST, collapseCrumbs, folderPath, searchFolders, shortPathLabel, visibleNodes } from "./folderTree";
import type { FolderNode, FolderTree, VisibleRow } from "./folderTree";
import { NO_DEPARTMENT, libraryHref } from "./libraryQuery";
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
  // Issue 409: Other opens the documents in no department (the filter the dashboard links to).
  // It is a place like a folder, so going anywhere else in the tree leaves it.
  const inOther = library.departmentId === NO_DEPARTMENT && library.folderId === null && !library.trash;
  const away = library.departmentId === NO_DEPARTMENT ? { ...library, departmentId: null } : library;
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [showAll, setShowAll] = useState<ReadonlySet<string>>(new Set());
  const current = library.folderId === null ? null : (tree.byId.get(library.folderId) ?? null);
  const toggle = (id: string, open: boolean) =>
    setExpanded((before) => {
      const next = new Set(before);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  const rows = (nodes: readonly FolderNode[]) =>
    visibleNodes(nodes, expanded, current, showAll).map((row) =>
      row.kind === "folder" ? (
        <FolderItem
          key={row.node.folder.id}
          row={row}
          library={away}
          onNavigate={onNavigate}
          onDrop={onDropDocument}
          onToggle={toggle}
        />
      ) : (
        <li key={`more-${row.parent?.folder.id ?? TOP_LIST}`} className={styles.treeRow} style={indent(row.depth)}>
          <span className={styles.treeToggle} aria-hidden="true" />
          <button
            type="button"
            className={`btn btn-quiet ${styles.treeMore}`}
            onClick={() => setShowAll((before) => new Set(before).add(row.parent?.folder.id ?? TOP_LIST))}
          >
            {row.parent
              ? `Show all ${formatNumber(row.parent.children.length)} folders in ${row.parent.folder.name}`
              : `Show all ${formatNumber(row.hidden + CHILD_CAP)} folders`}
          </button>
        </li>
      ),
    );
  return (
    <div className={styles.treeWrap}>
      {tree.byId.size > CHILD_CAP && <FindFolder tree={tree} library={away} onNavigate={onNavigate} />}
      <ul className={styles.tree}>
        <li>
          <Link
            href={libraryHref({ ...away, folderId: null, subfolders: false, trash: false })}
            scroll={false}
            className={styles.treeLink}
            aria-current={library.folderId === null && !library.trash && !inOther ? "page" : undefined}
            data-drop={top.over ? "true" : undefined}
            onClick={() => onNavigate("All documents")}
            {...top.handlers}
          >
            <TreeIcon name="documents" size="sm" className={styles.treeIcon} />
            All documents
          </Link>
        </li>
        {departments.length > 0 && (
          <li className={styles.treeGroup}>
            <span id="tree-departments" className={styles.groupHeading}>
              {FILES_TEXT.departments}
            </span>
            <ul className={styles.tree} aria-labelledby="tree-departments">
              {rows(departments)}
            </ul>
          </li>
        )}
        {departments.length === 0 ? (
          rows(others)
        ) : (
          <li className={styles.treeGroup}>
            <Link
              id="tree-other"
              href={libraryHref({ ...library, folderId: null, subfolders: false, trash: false, departmentId: NO_DEPARTMENT })}
              scroll={false}
              className={`${styles.treeLink} ${styles.groupLink}`}
              aria-current={inOther ? "page" : undefined}
              onClick={() => onNavigate(FILES_TEXT.notInDepartment)}
            >
              {FILES_TEXT.other}
            </Link>
            {others.length > 0 && (
              <>
                <span id="tree-outside" className={styles.groupSub}>
                  {FILES_TEXT.notInDepartment}
                </span>
                <ul className={styles.tree} aria-labelledby="tree-outside">
                  {rows(others)}
                </ul>
              </>
            )}
          </li>
        )}
        <li className={styles.trashItem}>
          <Link
            href={libraryHref({ ...away, folderId: null, subfolders: false, trash: true })}
            scroll={false}
            className={styles.treeLink}
            aria-current={library.trash ? "page" : undefined}
            onClick={() => onNavigate("Trash")}
          >
            <TreeIcon name="trash" size="sm" className={styles.treeIcon} />
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

/** The indent stops growing after this many levels; deeper rows say their level in words. */
export const INDENT_LEVELS = 8;

/** A tree row's indent: one step a level, none past INDENT_LEVELS. */
export const indent = (depth: number) => ({ "--depth": Math.min(depth - 1, INDENT_LEVELS) }) as CSSProperties;

/** A row's level from 2: in words past INDENT_LEVELS, else said to a screen reader only. */
export function TreeLevel({ depth }: { depth: number }) {
  if (depth > INDENT_LEVELS) return <span className={styles.treeLevel}>{`Level ${depth}`}</span>;
  return depth > 1 ? <span className="visually-hidden">{`, Level ${depth}`}</span> : null;
}

function FolderItem({
  row,
  library,
  onNavigate,
  onDrop,
  onToggle,
}: {
  row: Extract<VisibleRow, { kind: "folder" }>;
  library: LibraryState;
  onNavigate(placeName: string): void;
  onDrop: FolderNavProps["onDropDocument"];
  onToggle(id: string, open: boolean): void;
}) {
  const { node, open, hasChildren } = row;
  const here = library.folderId === node.folder.id;
  const target = useDropTarget(node.folder.id, node.folder.name, onDrop);
  return (
    <li className={styles.treeRow} style={indent(node.depth)} aria-level={node.depth}>
      {hasChildren ? (
        <button
          type="button"
          className={styles.treeToggle}
          aria-expanded={open}
          aria-label={`${open ? "Hide" : "Show"} folders in ${node.folder.name}`}
          onClick={() => onToggle(node.folder.id, !open)}
        >
          <TreeIcon name={open ? "chev-down" : "chev-right"} size="sm" />
        </button>
      ) : (
        <span className={styles.treeToggle} aria-hidden="true" />
      )}
      <Link
        href={libraryHref({ ...library, folderId: node.folder.id, trash: false })}
        scroll={false}
        className={styles.treeLink}
        aria-current={here ? "page" : undefined}
        data-drop={target.over ? "true" : undefined}
        onClick={() => onNavigate(node.folder.name)}
        {...target.handlers}
      >
        <TreeIcon name={isDepartment(node.folder) ? "department" : "folder"} size="sm" className={styles.treeIcon} />
        {node.folder.name}
        <TreeLevel depth={node.depth} />
      </Link>
    </li>
  );
}

/** "Find a folder": past CHILD_CAP folders, up to 50 whose names hold the words, each with its path. */
function FindFolder({
  tree,
  library,
  onNavigate,
}: {
  tree: FolderTree;
  library: LibraryState;
  onNavigate(placeName: string): void;
}) {
  const id = useId();
  const [words, setWords] = useState("");
  const found = searchFolders(tree, words);
  return (
    <div className={styles.treeFind}>
      <label htmlFor={id} className="visually-hidden">
        Find a folder
      </label>
      <input
        id={id}
        className="input"
        type="search"
        placeholder="Find a folder"
        value={words}
        autoComplete="off"
        onChange={(event) => setWords(event.target.value)}
      />
      {words.trim() !== "" && (
        <ul className={styles.tree} aria-label="Folders found">
          {found.length === 0 && <li className={`muted ${styles.note}`}>No folder has that name.</li>}
          {found.map((node) => (
            <li key={node.folder.id}>
              <Link
                href={libraryHref({ ...library, folderId: node.folder.id, trash: false })}
                scroll={false}
                className={styles.treeLink}
                title={folderPath(tree, node.folder.id)
                  .map((folder) => folder.name)
                  .join(" / ")}
                onClick={() => onNavigate(node.folder.name)}
              >
                {shortPathLabel(tree, node.folder.id)}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * The folder path above the place heading: All documents, then the folders above this one.
 * Deep down (#484) only the first and the last two show; the ones between fold into
 * "Show 26 more folders", which opens to list them as links.
 */
export function FolderCrumbs({ tree, library, folderId }: { tree: FolderTree; library: LibraryState; folderId: string }) {
  const above = folderPath(tree, folderId).slice(0, -1);
  const { head, hidden, tail } = collapseCrumbs(above);
  const crumb = (folder: { id: string; name: string }) => (
    <li key={folder.id}>
      <Link href={libraryHref({ ...library, folderId: folder.id })} scroll={false}>
        {folder.name}
      </Link>
    </li>
  );
  return (
    <nav aria-label="Folder path" className={styles.crumbs}>
      <ol>
        <li>
          <Link href={libraryHref({ ...library, folderId: null, subfolders: false })} scroll={false}>
            All documents
          </Link>
        </li>
        {head.map(crumb)}
        {hidden.length > 0 && (
          <li>
            <details className={styles.crumbsMore}>
              <summary>Show {hidden.length} more folders</summary>
              <ol>{hidden.map(crumb)}</ol>
            </details>
          </li>
        )}
        {tail.map(crumb)}
      </ol>
    </nav>
  );
}
