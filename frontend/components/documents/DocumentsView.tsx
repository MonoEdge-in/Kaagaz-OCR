"use client";

// The documents screen: the page header with "Upload files"; the folder tree
// (beside the list from 1024 px, in a drawer below that); where you are, with
// New folder, Rename, Move and Delete; the dropzone, which uploads into the
// folder being viewed; uploads in progress; search and the filter chips; the
// list and its pages. The folder and the filters live in the page address
// (libraryQuery.ts), so a reload keeps them and Back returns to the folder
// before. While any document is still being checked or read, the list is
// asked for again every 3 seconds; once all have settled, it stops.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { Drawer } from "@/components/ui/Drawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { ApiError, listDocuments, listFolders, listTags } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DocumentList, Folder, Tag } from "@/lib/types";
import { DocumentsTable } from "./DocumentsTable";
import { FolderActions } from "./FolderActions";
import { FolderNav } from "./FolderNav";
import { buildTree, folderPath } from "./folderTree";
import { noMatchTitle } from "./library";
import { LibraryFilters } from "./LibraryFilters";
import { ALL_DOCUMENTS, isFiltered, libraryHref, listParams } from "./libraryQuery";
import type { LibraryState } from "./libraryQuery";
import {
  ACCEPT_ATTRIBUTE,
  PAGE_SIZE,
  POLL_MS,
  deletedMessage,
  listView,
  offsetAfterDelete,
  pageInfo,
  shouldPoll,
} from "./logic";
import { Dropzone, UploadList, useUploads } from "./UploadArea";
import styles from "./Documents.module.css";
import libraryStyles from "./Library.module.css";

interface Loaded {
  view: string; // the view it belongs to (libraryHref)
  list: DocumentList;
  now: number; // when it arrived, for "5 minutes ago"
}

const SKELETON_ROWS = [0, 1, 2, 3, 4];
const DRAWER_ID = "documents-folders";

const errorText = (caught: unknown) =>
  caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.";

export function DocumentsView({ library }: { library: LibraryState }) {
  const { me } = useSession();
  const inputRef = useRef<HTMLInputElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const viewKey = libraryHref(library);
  const [draft, setDraft] = useState("");
  // The page belongs to the view it was turned in: a new folder or filter starts at page 1.
  const [search, setSearch] = useState({ view: viewKey, q: "", offset: 0 });
  const q = search.q;
  const offset = search.view === viewKey ? search.offset : 0;
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failure, setFailure] = useState<{ view: string; message: string } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const [folders, setFolders] = useState<Folder[] | null>(null);
  const [foldersError, setFoldersError] = useState<string | null>(null);
  const [foldersVersion, setFoldersVersion] = useState(0);
  const [tags, setTags] = useState<Tag[] | null>(null);
  const [tagsError, setTagsError] = useState<string | null>(null);
  const [tagsVersion, setTagsVersion] = useState(0);
  const [foldersOpen, setFoldersOpen] = useState(false);
  const uploads = useUploads(() => setReload((n) => n + 1));

  useEffect(() => {
    const hasActiveUpload = uploads.entries.some(
      (entry) => entry.state.kind === "waiting" || entry.state.kind === "uploading",
    );
    const hasCompletedUpload = uploads.entries.some(
      (entry) => entry.state.kind === "done" || entry.state.kind === "duplicate",
    );

    if (!hasActiveUpload && uploads.entries.length > 0 && hasCompletedUpload) {
      const timer = window.setTimeout(() => {
        window.location.reload();
      }, 250);
      return () => window.clearTimeout(timer);
    }
  }, [uploads.entries]);

  useEffect(() => {
    let cancelled = false;
    listFolders().then(
      (result) => {
        if (cancelled) return;
        setFolders(result.items);
        setFoldersError(null);
      },
      (caught: unknown) => {
        if (!cancelled) setFoldersError(errorText(caught));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [foldersVersion]);

  useEffect(() => {
    let cancelled = false;
    listTags().then(
      (result) => {
        if (cancelled) return;
        setTags(result.items);
        setTagsError(null);
      },
      (caught: unknown) => {
        if (!cancelled) setTagsError(errorText(caught));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [tagsVersion]);

  useEffect(() => {
    let cancelled = false;
    listDocuments(listParams(library, q, offset)).then(
      (list) => {
        if (cancelled) return;
        setLoaded({ view: viewKey, list, now: Date.now() });
        setFailure(null);
      },
      (caught: unknown) => {
        if (!cancelled) setFailure({ view: viewKey, message: errorText(caught) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [library, viewKey, q, offset, reload]);

  // A new folder or filter shows "loading" until its own list arrives.
  const shown = loaded && loaded.view === viewKey ? loaded : null;
  const error = failure && failure.view === viewKey ? failure.message : null;
  const items = shown?.list.items;
  useEffect(() => {
    if (!items || !shouldPoll(items)) return;
    const timer = setTimeout(() => setReload((n) => n + 1), POLL_MS);
    return () => clearTimeout(timer);
  }, [items]);

  const tree = useMemo(() => buildTree(folders ?? []), [folders]);
  const current = library.folderId === null ? null : (tree.byId.get(library.folderId) ?? null);
  const folderGone = folders !== null && library.folderId !== null && current === null;
  const filtered = isFiltered(library);
  // The folder in the address, even before the folder list has loaded.
  const uploadInto = library.folderId ?? undefined;

  const openPicker = () => inputRef.current?.click();
  const addFiles = (files: File[]) => void uploads.addFiles(files, uploadInto);

  function onChoose(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = ""; // so choosing the same file again still fires
    if (files.length > 0) addFiles(files);
  }

  // A filter or folder change rewrites the address in place. Next.js syncs
  // useSearchParams with history.replaceState without asking the server, so a
  // ticked box shows as ticked at once.
  function show(next: LibraryState) {
    window.history.replaceState(null, "", libraryHref(next));
  }

  function clearFilters() {
    show({ ...ALL_DOCUMENTS, folderId: library.folderId, subfolders: library.subfolders });
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearch({ view: viewKey, q: draft.trim(), offset: 0 });
  }

  function clearSearch() {
    setDraft("");
    setSearch({ view: viewKey, q: "", offset: 0 });
  }

  function turnPage(nextOffset: number) {
    setSearch({ view: viewKey, q, offset: nextOffset });
  }

  function afterDelete(name: string) {
    setAnnouncement(deletedMessage(name));
    headingRef.current?.focus();
    const nextOffset = offsetAfterDelete(offset, shown?.list.items.length ?? 0, PAGE_SIZE);
    if (nextOffset !== offset) turnPage(nextOffset);
    else setReload((n) => n + 1);
  }

  function afterSave(message: string) {
    setAnnouncement(message);
    setReload((n) => n + 1);
  }

  function afterFolderChange(message: string, goTo?: { folderId: string | null }) {
    setAnnouncement(message);
    setFoldersVersion((n) => n + 1);
    if (goTo) {
      show({ ...library, folderId: goTo.folderId, subfolders: goTo.folderId !== null && library.subfolders });
      headingRef.current?.focus();
    }
  }

  function navigated(placeName: string) {
    setFoldersOpen(false);
    setAnnouncement(`Showing ${placeName}.`);
  }

  const list = shown?.list;
  const view = listView(list ? list.total : null, error !== null, q, filtered);
  const pages = list ? pageInfo(offset, list.items.length, list.total) : null;
  // Search and filters stay while the next list loads, so an open filter panel stays open.
  const showControls = view === "list" || view === "no-match" || (loaded !== null && !shown);
  const placeName = current?.folder.name ?? (library.folderId === null ? "All documents" : "Folder");
  const above = current ? folderPath(tree, current.folder.id).slice(0, -1) : [];

  const nav = (
    <FolderNav
      tree={tree}
      library={library}
      loading={folders === null && foldersError === null}
      error={foldersError}
      onRetry={() => setFoldersVersion((n) => n + 1)}
      onNavigate={navigated}
    />
  );

  const uploadButton = (
    <button type="button" className="btn btn-primary" onClick={openPicker}>
      <Icon name="upload" />
      Upload files
    </button>
  );

  return (
    <div className={`page ${libraryStyles.libraryPage}`}>
      <header className="page-header">
        <div>
          <h1>Documents</h1>
          <p>Upload documents, file them in folders, then ask about them.</p>
        </div>
        {!folderGone && uploadButton}
      </header>

      <label htmlFor="upload-input" className="visually-hidden">
        Files to upload
      </label>
      <input
        ref={inputRef}
        id="upload-input"
        className="visually-hidden"
        type="file"
        multiple
        accept={ACCEPT_ATTRIBUTE}
        tabIndex={-1}
        onChange={onChoose}
      />

      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>

      <div className={libraryStyles.library}>
        <nav className={libraryStyles.treePane} aria-labelledby="folders-heading">
          <h2 id="folders-heading" className={libraryStyles.paneHeading}>
            Folders
          </h2>
          {nav}
        </nav>

        <section className={libraryStyles.main} aria-labelledby="place-heading">
          <div className={libraryStyles.placeHead}>
            <IconButton
              icon="documents"
              label="Folders"
              showLabel
              className={libraryStyles.foldersButton}
              aria-haspopup="dialog"
              aria-expanded={foldersOpen}
              aria-controls={DRAWER_ID}
              onClick={() => setFoldersOpen(true)}
            />
            <div className={libraryStyles.place}>
              {current && (
                <nav aria-label="Folder path" className={libraryStyles.crumbs}>
                  <ol>
                    <li>
                      <Link
                        href={libraryHref({ ...library, folderId: null, subfolders: false })}
                        scroll={false}
                      >
                        All documents
                      </Link>
                    </li>
                    {above.map((folder) => (
                      <li key={folder.id}>
                        <Link href={libraryHref({ ...library, folderId: folder.id })} scroll={false}>
                          {folder.name}
                        </Link>
                      </li>
                    ))}
                  </ol>
                </nav>
              )}
              <h2
                id="place-heading"
                ref={headingRef}
                tabIndex={-1}
                className={libraryStyles.placeHeading}
              >
                {placeName}
              </h2>
              {current && (
                <p className={libraryStyles.placeHint}>Files you upload here go into this folder.</p>
              )}
            </div>
            {folders !== null && !folderGone && (
              <FolderActions
                key={current?.folder.id ?? "all"}
                tree={tree}
                current={current}
                onChanged={afterFolderChange}
              />
            )}
          </div>

          {folderGone ? (
            <EmptyState
              icon="search-off"
              title="This folder isn't here any more"
              headingLevel={3}
              action={
                <Link
                  href={libraryHref({ ...library, folderId: null, subfolders: false })}
                  className="btn"
                  scroll={false}
                >
                  Show all documents
                </Link>
              }
            >
              It may have been deleted, or the link is old.
            </EmptyState>
          ) : (
            <>
              {view === "empty" ? (
                <Dropzone variant="full" onFiles={addFiles} onChoose={openPicker}>
                  <EmptyState
                    icon="file"
                    title={current ? "No documents in this folder yet" : "No documents yet"}
                    headingLevel={3}
                    action={uploadButton}
                  >
                    {current
                      ? "Upload files here, or move documents in with their Actions button."
                      : "Upload invoices, registers or bank statements, as PDF, photo or spreadsheet."}
                  </EmptyState>
                </Dropzone>
              ) : (
                <Dropzone variant="slim" onFiles={addFiles} onChoose={openPicker} />
              )}

              <UploadList entries={uploads.entries} onClearFinished={uploads.clearFinished} />

              {view !== "empty" && (
                <div className={styles.list}>
                  {showControls && (
                    <div className={styles.listHead}>
                      <form className={styles.search} role="search" onSubmit={submitSearch}>
                        <label htmlFor="documents-q" className="visually-hidden">
                          Search documents by name
                        </label>
                        <div className={styles.searchField}>
                          <Icon name="search" className={styles.searchIcon} />
                          <input
                            id="documents-q"
                            className={`input ${styles.searchInput}`}
                            type="search"
                            enterKeyHint="search"
                            autoComplete="off"
                            value={draft}
                            onChange={(event) => setDraft(event.target.value)}
                          />
                        </div>
                        <button type="submit" className="btn">
                          Search
                        </button>
                        {q !== "" && (
                          <button type="button" className="btn btn-quiet" onClick={clearSearch}>
                            Clear search
                          </button>
                        )}
                      </form>
                    </div>
                  )}

                  {showControls && (
                    <LibraryFilters
                      library={library}
                      tags={tags}
                      tagsError={tagsError}
                      onRetryTags={() => setTagsVersion((n) => n + 1)}
                      showSubfolders={current !== null && current.children.length > 0}
                      onChange={show}
                    />
                  )}

                  {error && (
                    <div className={styles.errorBanner} role="alert">
                      <Icon name="alert" className={styles.errorIcon} />
                      <div className={styles.errorBody}>
                        <p className={styles.errorTitle}>Could not load your documents.</p>
                        <p>{error}</p>
                      </div>
                      <button type="button" className="btn" onClick={() => setReload((n) => n + 1)}>
                        <Icon name="refresh" />
                        Try again
                      </button>
                    </div>
                  )}

                  {view === "loading" && (
                    <>
                      <p className="visually-hidden" role="status">
                        Loading your documents…
                      </p>
                      <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
                        {SKELETON_ROWS.map((row) => (
                          <span key={row} className={`skeleton ${styles.skeletonRow}`} />
                        ))}
                      </div>
                    </>
                  )}

                  {view === "no-match" && (
                    <EmptyState
                      icon="search-off"
                      title={noMatchTitle(q, filtered)}
                      headingLevel={3}
                      action={
                        <>
                          {q !== "" && (
                            <button type="button" className="btn" onClick={clearSearch}>
                              Clear search
                            </button>
                          )}
                          {filtered && (
                            <button type="button" className="btn" onClick={clearFilters}>
                              Clear filters
                            </button>
                          )}
                        </>
                      }
                    />
                  )}

                  {shown && view === "list" && shown.list.items.length > 0 && (
                    <DocumentsTable
                      items={shown.list.items}
                      me={me}
                      now={shown.now}
                      tree={tree}
                      tags={tags}
                      onDeleted={afterDelete}
                      onSaved={afterSave}
                      onFocusList={() => headingRef.current?.focus()}
                      onTagCreated={() => setTagsVersion((n) => n + 1)}
                    />
                  )}

                  {pages && list && view === "list" && (
                    <nav className={styles.pager} aria-label="Document pages">
                      <span className="muted tabular" aria-live="polite">
                        {pages.from}–{pages.to} of {list.total}
                      </span>
                      {list.total > PAGE_SIZE && (
                        <>
                          <button
                            type="button"
                            className="btn"
                            disabled={!pages.hasPrevious}
                            onClick={() => turnPage(Math.max(0, offset - PAGE_SIZE))}
                          >
                            <Icon name="chevron-left" />
                            Previous
                          </button>
                          <button
                            type="button"
                            className="btn"
                            disabled={!pages.hasNext}
                            onClick={() => turnPage(offset + PAGE_SIZE)}
                          >
                            Next
                            <Icon name="chevron-right" />
                          </button>
                        </>
                      )}
                    </nav>
                  )}
                </div>
              )}
            </>
          )}
        </section>
      </div>

      <Drawer id={DRAWER_ID} title="Folders" open={foldersOpen} onClose={() => setFoldersOpen(false)}>
        {nav}
      </Drawer>
    </div>
  );
}
