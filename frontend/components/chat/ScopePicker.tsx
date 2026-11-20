"use client";

// Which documents the next question may use (PRD 3 H2), in the composer's
// toolbar: a quiet chip saying the scope in words (said aloud once, by a separate
// status line, after a pick or when a conversation loads), which opens a small
// panel (ComposerMenu.tsx) with one labelled group of choices: all documents, one
// folder (with or without its subfolders), or the documents chat was opened
// for. #720: "Find a folder by name or path" picks a folder with its subfolders, and the
// chosen folder has a remove button beside the chip. A choice applies to the next question; until then a follow-up keeps
// the conversation's scope (scope.ts). Folders load only when needed. Folder
// and document names are plain text.

import { useEffect, useId, useState } from "react";
import type { ReactNode } from "react";
import { buildTree, inTreeOrder, pathLabel } from "@/components/documents/folderTree";
import { IconButton } from "@/components/ui/IconButton";
import { loadDocument } from "@/components/viewer/documentCache";
import { listFolders } from "@/lib/api";
import type { Folder } from "@/lib/types";
import { ComposerMenu } from "./ComposerMenu";
import { EVERY_DOCUMENT, findFolders, scopeAnnouncement, scopeText } from "./scope";
import type { ChosenScope, ScopeState } from "./scope";
import styles from "./Chat.module.css";

interface ChoiceProps {
  type?: "radio" | "checkbox";
  name?: string;
  checked: boolean;
  disabled?: boolean;
  onChange: () => void;
  children: ReactNode;
}

/** One labelled radio button (or checkbox) in the group. */
function Choice({ type = "radio", children, ...input }: ChoiceProps) {
  return (
    <label className={styles.menuOption}>
      <input type={type} {...input} />
      {children}
    </label>
  );
}

export function ScopePicker({
  state,
  locked,
  onChoose,
}: {
  state: ScopeState;
  locked: string | null; // why the choices can't be changed right now (scopeLockedText), or null
  onChoose: (scope: ChosenScope) => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [folders, setFolders] = useState<Folder[] | null>(null);
  const [foldersFailed, setFoldersFailed] = useState(false);
  const [find, setFind] = useState(""); // #720: "Find a folder by name or path"
  // name null: it could not be loaded (gone, or not this person's).
  const [named, setNamed] = useState<{ id: string; name: string | null } | null>(null);
  const { scope, documents } = state;

  const needFolders = (open || scope.kind === "folder") && folders === null && !foldersFailed;
  useEffect(() => {
    if (!needFolders) return;
    let cancelled = false;
    listFolders()
      .then((list) => {
        if (!cancelled) setFolders(list.items);
      })
      .catch(() => {
        if (!cancelled) setFoldersFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [needFolders]);

  // One document is named ("This document: bill.pdf"); its detail is cached.
  // Gone or not this person's, it stays "1 document" and the server answers as it can.
  const oneDocument = documents?.length === 1 ? documents[0] : null;
  useEffect(() => {
    if (!oneDocument) return;
    let cancelled = false;
    loadDocument(oneDocument)
      .then((detail) => {
        if (!cancelled) setNamed({ id: oneDocument, name: detail.display_name });
      })
      .catch(() => {
        if (!cancelled) setNamed({ id: oneDocument, name: null });
      });
    return () => {
      cancelled = true;
    };
  }, [oneDocument]);

  const tree = folders === null ? null : buildTree(folders);
  const name = named !== null && named.id === oneDocument ? named.name : null;
  const ordered = tree ? inTreeOrder(tree) : [];
  const firstFolder = ordered[0]?.folder.id;
  const found = tree ? findFolders(tree, find) : [];
  const chosenFolder = scope.kind === "folder" ? tree?.byId.get(scope.folderId)?.folder.name : undefined;
  const kind = `${id}-kind`;
  const text = scopeText(scope, tree, name);
  const namesLoading =
    (scope.kind === "folder" && folders === null && !foldersFailed) ||
    (scope.kind === "documents" && scope.ids.length === 1 && named?.id !== scope.ids[0]);

  return (
    <>
      {/* The visible chip is not a live region; this line speaks once per pick or restore. */}
      <p className="visually-hidden" role="status" aria-atomic="true">
        {scopeAnnouncement(state, text, namesLoading)}
      </p>
      <ComposerMenu label={text} name="Asking about" title="Which documents to ask about" onToggle={setOpen}>
        <fieldset
          className={styles.menuGroup}
          disabled={locked !== null}
          aria-describedby={`${id}-hint`}
        >
          <legend>Ask about</legend>
          <Choice name={kind} checked={scope.kind === "all"} onChange={() => onChoose(EVERY_DOCUMENT)}>
            All documents
          </Choice>
          <Choice
            name={kind}
            checked={scope.kind === "folder"}
            disabled={firstFolder === undefined}
            onChange={() => {
              if (firstFolder) onChoose({ kind: "folder", folderId: firstFolder, subfolders: false });
            }}
          >
            One folder
          </Choice>
          {tree && ordered.length > 0 ? (
            <div className={styles.scopeFolder}>
              <div className="field">
                <label htmlFor={`${id}-find`}>Find a folder by name or path</label>
                <input
                  id={`${id}-find`}
                  type="search"
                  className="input"
                  value={find}
                  onChange={(event) => setFind(event.target.value)}
                  aria-describedby={`${id}-find-hint`}
                />
              </div>
              {found.length > 0 ? (
                <ul className={styles.folderMatches} aria-label="Folders found">
                  {found.map((folder) => (
                    <li key={folder.id}>
                      <button
                        type="button"
                        className={`btn btn-quiet ${styles.folderMatch}`}
                        onClick={() => {
                          onChoose({ kind: "folder", folderId: folder.id, subfolders: true });
                          setFind("");
                        }}
                      >
                        <span className={styles.folderMatchName}>{folder.name}</span>
                        <span className={styles.sourceMeta}>{folder.path}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : find.trim() ? (
                <p className="hint" role="status">
                  No folder matches “{find.trim()}”.
                </p>
              ) : null}
              <p id={`${id}-find-hint`} className="hint">
                Only folders you can open are shown. Ask then searches that folder and its subfolders.
              </p>
            </div>
          ) : null}
          {scope.kind === "folder" && tree ? (
            <div className={styles.scopeFolder}>
              <div className="field">
                <label htmlFor={`${id}-folder`}>Folder</label>
                <select
                  id={`${id}-folder`}
                  className="input"
                  value={tree.byId.has(scope.folderId) ? scope.folderId : ""}
                  onChange={(event) => onChoose({ ...scope, folderId: event.target.value })}
                >
                  <option value="" disabled>
                    Choose a folder
                  </option>
                  {ordered.map((node) => (
                    <option key={node.folder.id} value={node.folder.id}>
                      {pathLabel(tree, node.folder.id)}
                    </option>
                  ))}
                </select>
              </div>
              <Choice
                type="checkbox"
                checked={scope.subfolders}
                onChange={() => onChoose({ ...scope, subfolders: !scope.subfolders })}
              >
                Include subfolders
              </Choice>
            </div>
          ) : null}
          {foldersFailed ? (
            <p className="error-text">
              Could not load the folders.{" "}
              <button type="button" className="btn btn-quiet" onClick={() => setFoldersFailed(false)}>
                Try again
              </button>
            </p>
          ) : open && folders === null ? (
            <p className="muted" role="status">
              Loading folders…
            </p>
          ) : folders?.length === 0 ? (
            <p className="hint">No folders yet. Make one in Documents.</p>
          ) : null}
          {documents ? (
            <Choice
              name={kind}
              checked={scope.kind === "documents"}
              onChange={() => onChoose({ kind: "documents", ids: documents })}
            >
              {scopeText({ kind: "documents", ids: documents }, null, name)}
            </Choice>
          ) : null}
          <p id={`${id}-hint`} className="hint">
            {locked ?? "Applies from your next question."}
          </p>
        </fieldset>
      </ComposerMenu>
      {chosenFolder !== undefined ? (
        <IconButton
          icon="close"
          label={`Remove folder ${chosenFolder}`}
          className={`btn-quiet ${styles.removeScope}`}
          disabled={locked !== null}
          onClick={() => onChoose(EVERY_DOCUMENT)}
        />
      ) : null}
    </>
  );
}
