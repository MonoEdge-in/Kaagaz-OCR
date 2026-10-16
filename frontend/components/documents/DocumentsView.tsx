"use client";

// The documents screen: the page header with "Upload files"; the folder tree
// (beside the list from 1024 px, in a drawer below that); where you are, with
// New folder, Rename, Move and Delete; search and the filter chips; the list and
// its pages. UI audit (16 Oct 2026): "Read with" has its room kept from the first
// paint, a skeleton until the limits come (D1); with documents listed, the whole
// screen is the drop target, an overlay saying so while files are dragged over it
// (D10); an empty list keeps its big dropzone. Files go into the folder being viewed.
// Uploads in progress are in the status bar, with the tray in a panel above it
// (UploadStatus), opened as files are added, so nothing pushes the list down.
// Search looks inside the documents (the default) or at their names (PRD 3 Q3, insideSearch.ts). The folder and the filters live in
// the page address (libraryQuery.ts), so a reload keeps them and Back returns to the folder
// before. While any document is still being checked or read, the list is
// asked for again (lib/poll.ts: every 3 s, then every 5 s, never while the
// tab is hidden); once all have settled, it stops. Each finished upload asks
// for it at once; the screen is never reloaded.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { canReview } from "@/components/review/review";
import { Drawer } from "@/components/ui/Drawer";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { UploadWords } from "@/components/words/UploadWords";
import { ApiError, listDocuments, listFolders, listTags, searchDocuments, updateDocument } from "@/lib/api";
import { progressSignature } from "@/lib/poll";
import { markReceived } from "@/lib/timeLeft";
import { useSession } from "@/lib/session";
import { usePoll } from "@/lib/usePoll";
import type { DocumentList, DocumentSearch, DocumentSummary, Folder, Tag } from "@/lib/types";
import { DocumentsTable, listScrollKey } from "./DocumentsTable";
import { FolderActions } from "./FolderActions";
import { FolderNav } from "./FolderNav";
import { buildTree, folderPath } from "./folderTree";
import { InsideSearchResults } from "./InsideResults";
import {
  DEFAULT_SEARCH_MODE,
  MAX_SEARCH_LENGTH,
  SEARCH_MODES,
  noResultsText,
  searchModeFor,
  searchParams,
} from "./insideSearch";
import type { SearchMode } from "./insideSearch";
import { libraryErrorText, noMatchTitle } from "./library";
import { mergeList } from "./listMerge";
import { LibraryFilters } from "./LibraryFilters";
import { includeSubfoldersHref, isFiltered, libraryHref, listParams } from "./libraryQuery";
import type { LibraryState } from "./libraryQuery";
import {
  ACCEPT_ATTRIBUTE,
  PAGE_SIZE,
  listView,
  offsetAfterDelete,
  pageInfo,
  shouldPoll,
  uploadsFinishedText,
} from "./logic";
import { foreverNeeds } from "./trash";
import { Dropzone, ReaderSelect, UploadStatus, useUploads } from "./UploadArea";
import { isBusy } from "./uploadQueue";
import styles from "./Documents.module.css";
import libraryStyles from "./Library.module.css";

interface Loaded {
  view: string; // the view it belongs to (libraryHref)
  list: DocumentList;
  now: number; // when it arrived, for "5 minutes ago"
}

const SKELETON_ROWS = [0, 1, 2, 3, 4];

// Back from a document: the browser restores the scroll against the short
// loading skeleton, so the list puts it back itself once its rows are in.
const back = { pending: false };
if (typeof window !== "undefined") {
  window.addEventListener("popstate", () => (back.pending = location.pathname === "/documents"));
}
const DRAWER_ID = "documents-folders";

const errorText = (caught: unknown) =>
  caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.";

export function DocumentsView({ library, startQ = "" }: { library: LibraryState; startQ?: string }) {
  const { me } = useSession();
  const inputRef = useRef<HTMLInputElement>(null);
  // "Upload new version": its own file input, and the document it is for.
  const versionInputRef = useRef<HTMLInputElement>(null);
  const versionOf = useRef<DocumentSummary | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const viewKey = libraryHref(library);
  // The search box keeps what is typed itself: a key press never re-renders the list.
  const searchRef = useRef<HTMLInputElement>(null);
  // The page belongs to the view it was turned in: a new folder or filter starts at page 1.
  const [search, setSearch] = useState({ view: viewKey, q: startQ, offset: 0 });
  // The header's search (G5) sends ?q=…: a new one starts that search here, its words in the box.
  const [heardQ, setHeardQ] = useState(startQ);
  if (startQ !== heardQ) {
    setHeardQ(startQ);
    if (startQ !== "") setSearch({ view: viewKey, q: startQ, offset: 0 });
  }
  const q = search.q;
  const offset = search.view === viewKey ? search.offset : 0;
  const [modeChoice, setModeChoice] = useState<SearchMode>(DEFAULT_SEARCH_MODE);
  const mode = searchModeFor(modeChoice, library);
  // Inside documents, the question goes to the search; the list below it is not narrowed by name.
  const listQ = mode === "inside" ? "" : q;
  const searchKey = mode === "inside" && q !== "" ? `${viewKey}|${q}` : null;
  const [found, setFound] = useState<{ key: string; result: DocumentSearch } | null>(null);
  const [searchFailure, setSearchFailure] = useState<{ key: string; message: string } | null>(null);
  const [searchAgain, setSearchAgain] = useState(0); // Try again; never the list's polling
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
  const [newFolderAsk, setNewFolderAsk] = useState(0); // D12: "+ New folder" in the folder column
  const [dropError, setDropError] = useState<string | null>(null);
  const uploads = useUploads(() => setReload((n) => n + 1));
  const [trayOpen, setTrayOpen] = useState(false);
  const filtered = isFiltered(library);

  // Each batch of uploads is said once, when it settles; nothing is cleared.
  const [heardBatches, setHeardBatches] = useState(0);
  if (uploads.settled !== heardBatches) {
    setHeardBatches(uploads.settled);
    setFoldersVersion((n) => n + 1); // a folder Kaagaz made for them (feature 28) shows in the tree

    if (uploads.finished) setAnnouncement(uploadsFinishedText(uploads.finished, q !== "" || filtered));
  }

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

  // Each answer is kept with the view it was asked for (one request at a time).
  const poll = usePoll<Loaded>({
    load: () =>
      listDocuments(listParams(library, listQ, offset)).then((list) => {
        markReceived(list.items, performance.now());
        return { view: viewKey, list, now: Date.now() };
      }),
    onResult: (result) => {
      // Unchanged rows keep their objects (listMerge.ts) and are memoised, so only rows
      // whose words change render again; the new `now` keeps "x minutes ago" moving.
      setLoaded((before) => {
        if (before === null || before.view !== result.view) return result;
        return { ...result, list: mergeList(before.list, result.list) };
      });
      setFailure(null);
    },
    onFailure: (caught) => setFailure({ view: viewKey, message: errorText(caught) }),
    again: (result) => shouldPoll(result.list.items),
    signature: (result) => result.view + progressSignature(result.list.items),
  });
  useEffect(() => poll.refresh(), [poll, library, viewKey, listQ, offset, reload]);

  useEffect(() => {
    if (searchKey === null) return;
    let cancelled = false;
    searchDocuments(searchParams(library, q)).then(
      (result) => {
        if (cancelled) return;
        setFound({ key: searchKey, result });
        setSearchFailure(null);
      },
      (caught: unknown) => {
        if (!cancelled) setSearchFailure({ key: searchKey, message: errorText(caught) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [library, q, searchKey, searchAgain]);
  const foundNow = found && found.key === searchKey ? found.result : null;
  const searchError = searchFailure && searchFailure.key === searchKey ? searchFailure.message : null;

  // A new folder or filter shows "loading" until its own list arrives.
  const shown = loaded && loaded.view === viewKey ? loaded : null;
  const error = failure && failure.view === viewKey ? failure.message : null;

  const scrollSettled = useRef(false);
  useEffect(() => {
    // Once, when the list first shows: a Back puts the saved place back; any other way
    // here drops it, so an old place can't come back later.
    if (!shown || scrollSettled.current) return;
    scrollSettled.current = true;
    if (!back.pending) {
      try {
        sessionStorage.removeItem(listScrollKey());
      } catch {
        // Storage blocked: nothing was kept.
      }
      return;
    }
    back.pending = false;
    try {
      const y = Number(sessionStorage.getItem(listScrollKey()));
      sessionStorage.removeItem(listScrollKey());
      if (y > 0) (document.getElementById("main") ?? window).scrollTo(0, y);
    } catch {
      // Storage blocked: nothing was kept.
    }
  }, [shown]);

  const tree = useMemo(() => buildTree(folders ?? []), [folders]);
  const current = library.folderId === null ? null : (tree.byId.get(library.folderId) ?? null);
  const folderGone = folders !== null && library.folderId !== null && current === null;
  // The folder in the address, even before the folder list has loaded.
  const uploadInto = library.folderId ?? undefined;

  const openPicker = () => inputRef.current?.click();
  const addFiles = (files: File[]) => {
    setTrayOpen(true);
    void uploads.addFiles(files, uploadInto);
  };

  function onChoose(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = ""; // so choosing the same file again still fires
    if (files.length > 0) addFiles(files);
  }

  // A filter or folder change rewrites the address in place. Next.js syncs
  // useSearchParams with history.replaceState without asking the server, so a
  // ticked box shows as ticked at once.
  // Called from the row's menu click, so the browser still allows opening the picker.
  function chooseVersion(document: DocumentSummary) {
    versionOf.current = document;
    versionInputRef.current?.click();
  }

  function onChooseVersion(event: ChangeEvent<HTMLInputElement>) {
    const [file] = Array.from(event.target.files ?? []);
    event.target.value = "";
    const of = versionOf.current;
    if (file && of) {
      setTrayOpen(true);
      void uploads.addVersion(file, of);
    }
  }

  function show(next: LibraryState) {
    window.history.replaceState(null, "", libraryHref(next));
  }

  function clearFilters() {
    show({ ...library, tagIds: [], type: null, from: null, to: null, attention: false });
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearch({ view: viewKey, q: (searchRef.current?.value ?? "").trim(), offset: 0 });
  }

  function clearSearch() {
    if (searchRef.current) searchRef.current.value = "";
    setSearch({ view: viewKey, q: "", offset: 0 });
  }

  function turnPage(nextOffset: number) {
    setSearch({ view: viewKey, q, offset: nextOffset });
    headingRef.current?.focus(); // the pager's button is disabled while the page loads
  }

  // Moved to Trash, restored or deleted for good: the row has left this list.
  function afterRemoved(message: string) {
    setAnnouncement(message);
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

  // D12: a document's name dropped on a folder in the tree moves it there.
  function dropDocument(documentId: string, folderId: string | null, placeName: string) {
    const name = shown?.list.items.find((item) => item.id === documentId)?.display_name ?? "The document";
    setDropError(null);
    updateDocument(documentId, { folder_id: folderId }).then(
      () => afterSave(`Moved ${name} to ${placeName}.`),
      (caught: unknown) => setDropError(libraryErrorText(caught, "document")),
    );
  }

  function navigated(placeName: string) {
    setFoldersOpen(false);
    setAnnouncement(`Showing ${placeName}.`);
  }

  const list = shown?.list;
  const view = listView(list ? list.total : null, error !== null, listQ, filtered);
  const pages = list ? pageInfo(offset, list.items.length, list.total) : null;
  // Search and filters stay while the next list loads, so an open filter panel stays open.
  const showControls = view === "list" || view === "no-match" || (loaded !== null && !shown);
  const placeName = library.trash
    ? "Trash"
    : (current?.folder.name ?? (library.folderId === null ? "All documents" : "Folder"));
  // Nothing is uploaded into Trash, and it has no folder to change.
  const canUpload = !folderGone && !library.trash;
  const above = current ? folderPath(tree, current.folder.id).slice(0, -1) : [];
  const withSubfolders = includeSubfoldersHref(library, current?.children.length ?? 0);

  const nav = (
    <FolderNav
      tree={tree}
      library={library}
      loading={folders === null && foldersError === null}
      error={foldersError}
      onRetry={() => setFoldersVersion((n) => n + 1)}
      onNavigate={navigated}
      onDropDocument={canUpload ? dropDocument : undefined}
    />
  );

  // An empty list shows this button in its empty state, and the header leaves it out. Secondary:
  // the app header's Upload is the screen's one primary (G5), and opens #upload-input too.
  const uploadButton = (
    <button type="button" className="btn" onClick={openPicker}>
      <Icon name="upload" />
      Upload<span className="narrow-hide"> files</span>
    </button>
  );
  // D1: until the limits come, a skeleton the select's size holds its place.
  const readerSelect = uploads.limitsLoaded ? (
    <ReaderSelect
      readers={uploads.limits.readers}
      picked={uploads.reader}
      onPick={uploads.chooseReader}
      ensemble={uploads.limits.ensemble ?? null}
      agents={uploads.agents}
      onAgents={uploads.chooseAgents}
    />
  ) : (
    <span className={`skeleton ${styles.readerSkeleton}`} aria-hidden="true" />
  );

  return (
    <Dropzone
      variant="page"
      limits={uploads.limits}
      onFiles={addFiles}
      enabled={canUpload && view !== "empty"}
      className={`page ${libraryStyles.libraryPage}`}
    >
      <header className="page-header">
        <div>
          <h1>Documents</h1>
          <p>Upload documents, file them in folders, then ask about them.</p>
        </div>
        {canUpload && (
          <div className={styles.uploadControls}>
            {readerSelect}
            {view !== "empty" && uploadButton}
          </div>
        )}
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
      <label htmlFor="version-input" className="visually-hidden">
        File for the new version
      </label>
      <input
        ref={versionInputRef}
        id="version-input"
        className="visually-hidden"
        type="file"
        accept={ACCEPT_ATTRIBUTE}
        tabIndex={-1}
        onChange={onChooseVersion}
      />

      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>

      <div className={libraryStyles.library}>
        <nav className={libraryStyles.treePane} aria-labelledby="folders-heading">
          <div className={libraryStyles.paneHead}>
            <h2 id="folders-heading" className={libraryStyles.paneHeading}>
              Folders
            </h2>
            {folders !== null && canUpload && (
              <IconButton icon="plus" label="New folder" onClick={() => setNewFolderAsk((n) => n + 1)} />
            )}
          </div>
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
              {library.trash && (
                <p className={libraryStyles.placeHint}>
                  Documents in Trash are left out of search and chat. Restore one to use it again.
                </p>
              )}
              {library.trash && foreverNeeds(me) === "admin_sign_in" && (
                <p className={libraryStyles.placeHint}>
                  To delete forever, <Link href="/login">sign in again</Link>.
                </p>
              )}
              {library.trash && foreverNeeds(me) === "not_allowed" && (
                <p className={libraryStyles.placeHint}>
                  Only admins, and people an admin allows, can delete documents forever.
                </p>
              )}
              {library.trash && foreverNeeds(me) === "two_step" && (
                <p className={libraryStyles.placeHint}>
                  To delete forever, turn on two-step sign-in in <Link href="/admin">Admin, Settings</Link>.
                </p>
              )}
            </div>
            {folders !== null && canUpload && (
              <FolderActions
                key={current?.folder.id ?? "all"}
                tree={tree}
                current={current}
                onChanged={afterFolderChange}
                askNew={newFolderAsk}
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
              {library.trash ? (
                view === "empty" && (
                  <EmptyState icon="trash" title="Trash is empty" headingLevel={3}>
                    Documents moved to Trash show here, until they are restored or deleted for good.
                  </EmptyState>
                )
              ) : view === "empty" ? (
                <Dropzone
                  variant="full"
                  limits={uploads.limits}
                  onFiles={addFiles}
                >
                  <EmptyState
                    icon="file"
                    title={current ? "No documents in this folder yet" : "No documents yet"}
                    headingLevel={3}
                    action={
                      <>
                        {uploadButton}
                        {withSubfolders && (
                          <Link href={withSubfolders} className="btn" scroll={false}>
                            Include subfolders
                          </Link>
                        )}
                      </>
                    }
                  >
                    {current
                      ? "Upload files here, or move documents in from their menu."
                      : "Upload invoices, registers or bank statements, as PDF, photo or spreadsheet."}
                  </EmptyState>
                </Dropzone>
              ) : null}

              {!library.trash && <UploadWords entries={uploads.entries} settled={uploads.settled} />}

              {view !== "empty" && (
                <div className={styles.list}>
                  {showControls && (
                    <div className={styles.listHead}>
                      <form className={styles.search} role="search" onSubmit={submitSearch}>
                        <label htmlFor="documents-q" className={styles.searchLabel}>
                          {mode === "inside" ? "Search inside documents" : "Search documents by name"}
                        </label>
                        <div className={styles.searchField}>
                          <Icon name="search" size={16} className={styles.searchIcon} />
                          <input
                            id="documents-q"
                            className={`input ${styles.searchInput}`}
                            type="search"
                            enterKeyHint="search"
                            autoComplete="off"
                            maxLength={MAX_SEARCH_LENGTH}
                            placeholder="e.g. Sharma Industries invoice"
                            ref={searchRef}
                            key={heardQ}
                            defaultValue={q}
                          />
                          {/* D6: clearing is an x inside the box; the box and Search share one height. */}
                          {q !== "" && (
                            <IconButton icon="close" label="Clear search" className={styles.searchClear} onClick={clearSearch} />
                          )}
                        </div>
                        <button type="submit" className="btn">
                          Search
                        </button>
                        {!library.trash && (
                          <fieldset className={styles.searchMode}>
                            <legend className="visually-hidden">Search in</legend>
                            {SEARCH_MODES.map((choice) => (
                              <label key={choice.mode} className={libraryStyles.check}>
                                <input
                                  type="radio"
                                  name="documents-search-mode"
                                  checked={mode === choice.mode}
                                  onChange={() => setModeChoice(choice.mode)}
                                />
                                {choice.label}
                              </label>
                            ))}
                          </fieldset>
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

                  {dropError && (
                    <p className="error-text" role="alert">
                      {dropError}
                    </p>
                  )}

                  {error && shown && (
                    <div className={styles.errorBanner} role="status">
                      <Icon name="alert" className={styles.errorIcon} />
                      <p className={styles.errorBody}>Couldn&apos;t update the list. It may be out of date.</p>
                      <button type="button" className="btn" onClick={() => setReload((n) => n + 1)}>
                        <Icon name="refresh" />
                        Try again
                      </button>
                    </div>
                  )}

                  {error && !shown && (
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

                  {searchKey === null && view === "no-match" && (
                    <EmptyState
                      icon="search-off"
                      title={noMatchTitle(q, filtered, library.attention && !isFiltered({ ...library, attention: false }))}
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

                  {searchKey !== null && (
                    <InsideSearchResults
                      found={foundNow}
                      error={searchError}
                      q={q}
                      noResults={noResultsText(library)}
                      datesIgnored={library.from !== null || library.to !== null}
                      onRetry={() => {
                        setSearchFailure(null); // back to "Searching…" until the new reply
                        setSearchAgain((n) => n + 1);
                      }}
                      onClear={clearSearch}
                    />
                  )}

                  {searchKey === null && shown && view === "list" && shown.list.items.length > 0 && (
                    <DocumentsTable
                      items={shown.list.items}
                      me={me}
                      now={shown.now}
                      tree={tree}
                      tags={tags}
                      inTrash={library.trash}
                      onRemoved={afterRemoved}
                      onSaved={afterSave}
                      onFocusList={() => headingRef.current?.focus()}
                      onTagCreated={() => setTagsVersion((n) => n + 1)}
                      onUploadVersion={chooseVersion}
                    />
                  )}

                  {searchKey === null && pages && list && view === "list" && (
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

      <UploadStatus
        open={trayOpen}
        onOpenChange={setTrayOpen}
        entries={uploads.entries}
        views={uploads.views}
        stored={uploads.stored}
        tooMany={uploads.tooMany}
        onClearFinished={() => {
          uploads.clearFinished();
          // Nothing left: the panel and its button go, so focus goes to the list's heading.
          if (!uploads.entries.some(isBusy)) {
            setTrayOpen(false);
            headingRef.current?.focus();
          }
        }}
        onRestore={(key, documentId, name) => void uploads.restore(key, documentId, name)}
        onRetry={(key) => void uploads.retry(key)}
        tree={tree}
        onUndone={() => setReload((n) => n + 1)}
        canReread={canReview(me.user.role)}
      />
    </Dropzone>
  );
}
