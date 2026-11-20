"use client";

// The "Ask about" picker's find box (#720, #725): one box that finds a department, a folder or a
// file by name. Matches are grouped under plain headings, Departments, then Folders, then Files,
// each row with its own icon and its place in grey, so a first-time user always sees which kind
// they are choosing. A department or folder asks in it and its subfolders; files toggle in and
// out of the files asked about (up to MAX_FILES). Files come from GET /documents (?q=, by name,
// only what this person can open), asked once typing pauses; folders from the tree already loaded.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon/Icon";
import type { KIconName } from "@/components/ui/Icon/Icon";
import type { FolderTree } from "@/components/documents/folderTree";
import { listDocuments } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";
import { EVERY_DOCUMENT, filePlace, findFolders, pickFile } from "./scope";
import type { ChatScope, ChosenScope } from "./scope";
import styles from "./Chat.module.css";

export const FILES_SHOWN = 10;
const FIND_WAIT_MS = 300; // typing has paused
const FIND_FROM = 2; // letters before files are looked up

interface Row {
  id: string;
  name: string;
  place: string;
  icon: KIconName;
  pressed?: boolean;
  disabled?: boolean;
  onPick: () => void;
}

function Group({ heading, rows }: { heading: string; rows: Row[] }) {
  if (rows.length === 0) return null;
  return (
    <div>
      <h3 className={styles.findHeading}>{heading}</h3>
      <ul className={styles.folderMatches} aria-label={heading}>
        {rows.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              className={`btn btn-quiet ${styles.folderMatch}`}
              aria-pressed={row.pressed}
              disabled={row.disabled}
              onClick={row.onPick}
            >
              <Icon name={row.icon} size="sm" />
              <span className={styles.folderMatchText}>
                <span className={styles.folderMatchName}>{row.name}</span>
                <span className={styles.sourceMeta}>{row.place}</span>
              </span>
              {row.pressed ? <Icon name="check" size="sm" /> : null}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ScopeFind({
  id,
  tree,
  scope,
  onChoose,
}: {
  id: string;
  tree: FolderTree;
  scope: ChatScope;
  onChoose: (scope: ChosenScope) => void;
}) {
  const [find, setFind] = useState("");
  const [files, setFiles] = useState<{ query: string; items: DocumentSummary[] } | null>(null);
  const [failed, setFailed] = useState<string | null>(null); // the query whose file lookup failed
  const query = find.trim();

  useEffect(() => {
    if (query.length < FIND_FROM) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      listDocuments({ q: query, limit: FILES_SHOWN, status: "ready" })
        .then(({ items }) => {
          if (!cancelled) setFiles({ query, items });
        })
        .catch(() => {
          if (!cancelled) setFailed(query);
        });
    }, FIND_WAIT_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  const places = findFolders(tree, query);
  const inPlace = (folderId: string) => () => {
    onChoose({ kind: "folder", folderId, subfolders: true });
    setFind("");
  };
  const placeRow = (icon: KIconName) => (place: (typeof places)[number]): Row => ({
    id: place.id,
    name: place.name,
    place: place.path,
    icon,
    onPick: inPlace(place.id),
  });
  const picked = scope.kind === "documents" ? scope.ids : [];
  const fileRows: Row[] = (files?.query === query ? files.items : []).map((file) => ({
    id: file.id,
    name: file.display_name,
    place: filePlace(tree, file),
    icon: "file",
    pressed: picked.includes(file.id),
    disabled: pickFile(picked, file.id) === null,
    onPick: () => {
      const ids = pickFile(picked, file.id);
      if (ids) onChoose(ids.length > 0 ? { kind: "documents", ids } : EVERY_DOCUMENT);
    },
  }));
  const looking = query.length >= FIND_FROM && files?.query !== query && failed !== query;
  const nothing = query.length >= FIND_FROM && !looking && places.length === 0 && fileRows.length === 0;

  return (
    <div className={styles.scopeFolder}>
      <div className="field">
        <label htmlFor={`${id}-find`}>Find where to ask</label>
        <input
          id={`${id}-find`}
          type="search"
          className="input"
          value={find}
          onChange={(event) => setFind(event.target.value)}
          aria-describedby={`${id}-find-hint`}
        />
      </div>
      <p id={`${id}-find-hint`} className="hint">
        Find a department, folder or file by name.
      </p>
      <Group heading="Departments" rows={places.filter((place) => place.department).map(placeRow("department"))} />
      <Group heading="Folders" rows={places.filter((place) => !place.department).map(placeRow("folder"))} />
      <Group heading="Files" rows={fileRows} />
      {looking ? (
        <p className="muted" role="status">
          Looking for files…
        </p>
      ) : null}
      {failed === query && query !== "" ? <p className="error-text">Could not look for files right now.</p> : null}
      {nothing ? (
        <p className="hint" role="status">
          No department, folder or file matches “{query}”. To ask about everything, choose All documents above.
        </p>
      ) : null}
    </div>
  );
}
