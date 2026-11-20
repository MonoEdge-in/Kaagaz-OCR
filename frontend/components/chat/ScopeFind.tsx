"use client";

// The "Ask about" picker's find box (#720, #725): one box that finds a department, a folder or a
// file by name. Matches are grouped under plain headings, Departments, then Folders, then Files,
// each row with its own icon and its place in grey, so a first-time user always sees which kind
// they are choosing. A department or folder asks in it and its subfolders; files toggle in and
// out of the files asked about (up to MAX_FILES). Files come from GET /documents (?q=, by name,
// only what this person can open), asked once typing pauses; folders from the tree already loaded.
// The files chosen stay listed under "Chosen files", below the results (and how many more were
// chosen before the box showed them); a list cut short, or the 50-file cap, says so.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon/Icon";
import type { KIconName } from "@/components/ui/Icon/Icon";
import type { FolderTree } from "@/components/documents/folderTree";
import { listDocuments } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";
import { EVERY_DOCUMENT, MAX_FILES, filePlace, findFolders, pickFile } from "./scope";
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
  const [files, setFiles] = useState<{ query: string; items: DocumentSummary[]; total: number } | null>(null);
  const [failed, setFailed] = useState<string | null>(null); // the query whose file lookup failed
  // Every file shown so far, by id, so the chosen ones stay listed when the search moves on.
  const [seen, setSeen] = useState<ReadonlyMap<string, DocumentSummary>>(new Map());
  const query = find.trim();

  useEffect(() => {
    if (query.length < FIND_FROM) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      listDocuments({ q: query, limit: FILES_SHOWN, status: "ready" })
        .then(({ items, total }) => {
          if (cancelled) return;
          setFiles({ query, items, total });
          setFailed(null);
          setSeen((known) => new Map([...known, ...items.map((item) => [item.id, item] as const)]));
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
  const fileRow = (file: DocumentSummary): Row => ({
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
  });
  const current = files?.query === query ? files : null;
  const fileRows = (current?.items ?? []).map(fileRow);
  const chosen = picked.flatMap((fileId) => {
    const file = seen.get(fileId);
    return file ? [fileRow(file)] : [];
  });
  const earlier = picked.length - chosen.length; // chosen before this box showed them (a link, a restore)
  const lookedUp = failed !== query;
  const looking = query.length >= FIND_FROM && current === null && lookedUp;
  const nothing = query.length >= FIND_FROM && lookedUp && !looking && places.length === 0 && fileRows.length === 0;

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
      {current && current.total > current.items.length ? (
        <p className="hint">
          Showing {current.items.length} of {current.total} files. Type more of the name to narrow it.
        </p>
      ) : null}
      <Group heading="Chosen files" rows={chosen} />
      {earlier > 0 ? (
        <p className="hint">
          {chosen.length > 0 ? `And ${earlier} more` : earlier} {earlier === 1 ? "file" : "files"} chosen earlier.
        </p>
      ) : null}
      {picked.length >= MAX_FILES ? <p className="hint">You can ask about up to {MAX_FILES} files at once.</p> : null}
      {looking ? (
        <p className="muted" role="status">
          Looking for files…
        </p>
      ) : null}
      {!lookedUp && query !== "" ? <p className="error-text">Could not look for files right now.</p> : null}
      {nothing ? (
        <p className="hint" role="status">
          No department, folder or file matches “{query}”. To ask about everything, choose All documents above.
        </p>
      ) : null}
    </div>
  );
}
