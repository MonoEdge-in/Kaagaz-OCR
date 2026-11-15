"use client";

// The documents screen: the page header (the app header's Upload is the one upload action,
// 16 Oct 2026; an empty list's empty state has "Upload files"); the folder tree (beside the
// list from 1024 px, stacked above it below that), its "+" the one New folder; where you
// are, with Rename, Move and Delete, and "Read with" at the list's right edge (it sets how
// the next uploads are read, from either Upload button or a drop); search inside the
// documents and the filter chips; the list and its pages. UI audit (16 Oct 2026): "Read
// with" has its room kept from the first paint, a skeleton until the limits come (D1); with documents listed, the whole
// screen is the drop target, an overlay saying so while files are dragged over it
// (D10); an empty list keeps its big dropzone. Files go into the folder being viewed.
// Uploads in progress are in the status bar, with the tray in a panel above it
// (UploadStatus), opened as files are added, so nothing pushes the list down.
// Search looks inside the documents (the default) or at their names (PRD 3 Q3, insideSearch.ts);
// the app header's search (?q=) finds a document by its name. The folder and the filters live in
// the page address (libraryQuery.ts), so a reload keeps them and Back returns to the folder
// before. While any document is still being checked or read, the list is
// asked for again (lib/poll.ts: every 3 s, then every 5 s, never while the
// tab is hidden); once all have settled, it stops. Each finished upload asks
// for it at once; the screen is never reloaded.
// A move to a folder (a row's menu, the selection bar or a drop on a folder) says so in a toast, with Undo; the New
// folder forms offer names from the kinds of documents listed (folderNameSuggestions).
// Trash (#542): a folder deleted with everything inside is one row of it; opened, it shows its
// folders and files read only, under "Trash › Accounts › 2025"; Restore and Delete forever say so
// in the toast, and the folder tree is asked for again. Search, filters and pages are for files.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { ASK_FOR_ACCESS, asksForAccess } from "@/components/access/askForAccess";
import { DepartmentPicker } from "@/components/files/DepartmentPicker";
import { chosenDepartment, departmentsOf, rememberDepartment } from "@/components/files/departments";
import { DocumentsGrid } from "@/components/files/DocumentsGrid";
import { FolderTiles } from "@/components/files/FolderTiles";
import { readViewMode, saveViewMode } from "@/components/files/viewMode";
import type { ViewMode } from "@/components/files/viewMode";
import { FILES_TEXT } from "@/components/files/words";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { Icon as KitIcon } from "@/components/ui/Icon/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { RadioPill, Segmented } from "@/components/ui/Segmented/Segmented";
import { useSharedUploads } from "@/components/shell/Uploads";
import { Toast } from "@/components/ui/Toast";
import { VersionNoteDialog } from "@/components/versions/VersionNoteDialog";
import {
  ApiError,
  getTrashedFolder,
  listDocuments,
  listFolders,
  listPeople,
  listTags,
  listTrashedFolders,
  searchDocuments,
  updateDocument,
} from "@/lib/api";
import { progressSignature } from "@/lib/poll";
import { markReceived } from "@/lib/timeLeft";
import { useSession } from "@/lib/session";
import { usePoll } from "@/lib/usePoll";
import type {
  DocumentList,
  DocumentSearch,
  DocumentSummary,
  Folder,
  Person,
  Tag,
  TrashedFolder,
  TrashedFolderContents,
} from "@/lib/types";
import { DocumentsTable, listScrollKey } from "./DocumentsTable";
import { FolderActions } from "./FolderActions";
import { FolderCrumbs, FolderNav, TrashCrumbs } from "./FolderNav";
import { buildTree } from "./folderTree";
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
import { folderNameSuggestions, libraryErrorText, noMatchTitle } from "./library";
import { mergeList } from "./listMerge";
import { LibraryFilters } from "./LibraryFilters";
import { NO_DEPARTMENT, NO_FILTERS, SORT_KEYS, includeSubfoldersHref, isFiltered, libraryHref, listParams, nextSort } from "./libraryQuery";
import type { LibraryState, SortKey } from "./libraryQuery";
import {
  ACCEPT_ATTRIBUTE,
  PAGE_SIZE,
  listView,
  offsetAfterDelete,
  pageInfo,
  shouldPoll,
  uploadsFinishedText,
} from "./logic";
import { foreverNeeds, trashEmpty } from "./trash";
import type { RemovalUndo } from "./trash";
import { Dropzone, ReaderSelect } from "./UploadArea";
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

const errorText = (caught: unknown) =>
  caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.";

export function DocumentsView({ library, startQ = "" }: { library: LibraryState; startQ?: string }) {
  const { me } = useSession();
  const inputRef = useRef<HTMLInputElement>(null);
  // "Upload new version": its own file input, and the document it is for.
  const versionInputRef = useRef<HTMLInputElement>(null);
  const sortInputRef = useRef<HTMLInputElement>(null);
  const versionOf = useRef<DocumentSummary | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  // #542: the folder in Trash that is open (read only); Trash's top level when null.
  // shortcut: not in the address, so Back does not step up a level; the path above the heading does.
  const [trashOpenChoice, setTrashOpen] = useState<string | null>(null);
  if (!library.trash && trashOpenChoice !== null) setTrashOpen(null); // left Trash: it opens at its top again
  const trashOpen = library.trash ? trashOpenChoice : null;
  const viewKey = libraryHref(library) + (trashOpen === null ? "" : `|trash:${trashOpen}`);
  // The search box keeps what is typed itself: a key press never re-renders the list.
  const searchRef = useRef<HTMLInputElement>(null);
  // The page belongs to the view it was turned in: a new folder or filter starts at page 1.
  const [search, setSearch] = useState({ view: viewKey, q: startQ, offset: 0 });
  // The header's search (G5) sends ?q=…: a new one starts that search here, its words in the box.
  // The header finds a document by its name, so its words search the names (Tushar, 16 Oct 2026).
  const [heardQ, setHeardQ] = useState(startQ);
  const [modeChoice, setModeChoice] = useState<SearchMode>(startQ === "" ? DEFAULT_SEARCH_MODE : "names");
  if (startQ !== heardQ) {
    setHeardQ(startQ);
    if (startQ !== "") {
      setSearch({ view: viewKey, q: startQ, offset: 0 });
      setModeChoice("names");
    }
  }
  const q = search.q;
  const offset = search.view === viewKey ? search.offset : 0;
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
  const [newFolderAsk, setNewFolderAsk] = useState(0); // D12: the folder column's "+", the one New folder
  const [dropError, setDropError] = useState<string | null>(null);
  // undone: the words once Undo worked ("Moved back.", "Restored from Trash."). No undo: a note only (#537).
  const [toast, setToast] = useState<{ message: string; undo?: () => Promise<void>; undone?: string } | null>(null);
  // #542: Trash's folders (top level), or the open one's; with the view they belong to.
  const [trashFolders, setTrashFolders] = useState<{
    view: string;
    items: TrashedFolder[];
    opened: TrashedFolderContents | null;
    failed?: string; // they did not load: said, with Try again, never shown as an empty Trash
  } | null>(null);
  const toastUndo = toast?.undo;
  // The shell's one upload queue and tray (D-12); this screen adds files into the folder shown.
  const { uploads, setTrayOpen, folders: folderPicking, foldersChanged, foldersEdited } = useSharedUploads(() =>
    setReload((n) => n + 1),
  );
  const filtered = isFiltered(library);
  // File management: the people for the Owner filter, List or Grid (per person, F5), the
  // department picked for uploads outside any folder (F2), and the file waiting for its note (V9).
  const [people, setPeople] = useState<Person[] | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>(() => readViewMode(me.user.id));
  const [departmentChoice, setDepartmentChoice] = useState<string | null | undefined>(undefined);
  const [versionFile, setVersionFile] = useState<{ file: File; of: DocumentSummary } | null>(null);

  useEffect(() => {
    let cancelled = false;
    listPeople().then(
      (reply) => {
        if (!cancelled) setPeople(reply.items);
      },
      () => {
        if (!cancelled) setPeople([]); // the Owner filter offers Anyone only
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  // Each batch of uploads is said once, when it settles; nothing is cleared.
  const [heardBatches, setHeardBatches] = useState(0);
  if (uploads.settled !== heardBatches) {
    setHeardBatches(uploads.settled);
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
  }, [foldersVersion, foldersChanged]); // foldersChanged (#536): a folder upload's or Kaagaz's new folders show at once

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
    if (!library.trash) return;
    let cancelled = false;
    const view = viewKey;
    const asked =
      trashOpen === null
        ? listTrashedFolders().then((reply) => ({ view, items: reply.items, opened: null }))
        : getTrashedFolder(trashOpen).then((opened) => ({ view, items: opened.folders, opened }));
    asked.then(
      (result) => {
        if (!cancelled) setTrashFolders(result);
      },
      (caught: unknown) => {
        if (cancelled) return;
        // The open folder is no longer in Trash (restored, deleted or purged): back to Trash.
        if (trashOpen !== null && caught instanceof ApiError && caught.status === 404) setTrashOpen(null);
        else setTrashFolders({ view, items: [], opened: null, failed: errorText(caught) }); // the files still show
      },
    );
    return () => {
      cancelled = true;
    };
  }, [library.trash, trashOpen, viewKey, reload]);

  // Each answer is kept with the view it was asked for (one request at a time).
  const poll = usePoll<Loaded>({
    load: () =>
      listDocuments({ ...listParams(library, listQ, offset), trashed_folder_id: trashOpen ?? undefined }).then((list) => {
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
  const shownItems = shown?.list.items;
  const nameSuggestions = useMemo(
    () => folderNameSuggestions(shownItems ?? [], folders ?? []),
    [shownItems, folders],
  );
  const current = library.folderId === null ? null : (tree.byId.get(library.folderId) ?? null);
  const folderGone = folders !== null && library.folderId !== null && current === null;
  // The folder in the address, even before the folder list has loaded.
  const uploadInto = library.folderId ?? undefined;
  // F2: outside any folder, the files go into the department picked (the last one used).
  const departments = useMemo(() => departmentsOf(folders ?? []), [folders]);
  const department = chosenDepartment(departmentChoice, departments);

  const openPicker = () => inputRef.current?.click();
  // #484: a whole folder (picked or dropped) keeps its structure, inside the folder shown at first.
  const dropFolders = (entries: FileSystemDirectoryEntry[]) => folderPicking.drop(entries, library.folderId);
  // Option 2, "Let Kaagaz sort them": loose files only, with no folder and no department, so
  // auto-filing reads each one and files it, making the folders it needs.
  function onChooseToSort(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;
    setTrayOpen(true);
    void uploads.addFiles(files);
  }
  const addFiles = (files: File[]) => {
    setTrayOpen(true);
    void uploads.addFiles(files, uploadInto, uploadInto === undefined ? (department ?? undefined) : undefined);
  };

  function pickDepartment(id: string | null) {
    setDepartmentChoice(id);
    rememberDepartment(id);
  }

  function chooseView(mode: ViewMode) {
    setViewMode(mode);
    saveViewMode(me.user.id, mode);
  }

  // Q4: a header pressed sorts the list by it, or flips the order.
  function sortBy(key: SortKey) {
    show({ ...library, ...nextSort(library, key) });
  }

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
    if (file && of) setVersionFile({ file, of }); // V9: its note first, then it goes
  }

  function uploadVersionWith(note: string | undefined) {
    if (!versionFile) return;
    setVersionFile(null);
    setTrayOpen(true);
    void uploads.addVersion(versionFile.file, versionFile.of, note);
  }

  function show(next: LibraryState) {
    window.history.replaceState(null, "", libraryHref(next));
  }

  function clearFilters() {
    show({ ...library, ...NO_FILTERS });
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

  // Moved to Trash, restored or deleted for good: the row has left this list. A move to Trash
  // or a restore says so in the toast, with Undo, as a move to a folder does (issue 372).
  function afterRemoved(message: string, undo?: RemovalUndo) {
    if (undo) setToast({ message, undo: undo.run, undone: undo.done });
    else setAnnouncement(message);
    if (library.trash) foldersEdited(); // #542: a restore may bring folders back; B4: the shell's list too
    headingRef.current?.focus();
    const nextOffset = offsetAfterDelete(offset, shown?.list.items.length ?? 0, PAGE_SIZE);
    if (nextOffset !== offset) turnPage(nextOffset);
    else setReload((n) => n + 1);
  }

  // A move shows in the toast (its own live region), with Undo; anything else is announced.
  function afterSave(message: string, undo?: () => Promise<void>) {
    if (undo) setToast({ message, undo, undone: "Moved back." });
    else setAnnouncement(message);
    setReload((n) => n + 1);
  }

  function undoMove(undo: () => Promise<void>, undone: string) {
    setDropError(null);
    undo().then(
      () => afterSave(undone),
      (caught: unknown) => {
        setDropError(libraryErrorText(caught, "document"));
        setReload((n) => n + 1);
      },
    );
  }

  // #542: a folder in Trash restored or deleted for good from its row: said in the toast.
  function afterTrashFolder(message: string) {
    setToast({ message });
    foldersEdited(); // B4: this screen's folder list and the shell's ask again
    setReload((n) => n + 1);
    headingRef.current?.focus();
  }

  function openTrashFolder(id: string | null) {
    setTrashOpen(id);
    headingRef.current?.focus();
  }

  function afterFolderChange(message: string, goTo?: { folderId: string | null }, asToast = false) {
    if (asToast) setToast({ message });
    else setAnnouncement(message);
    foldersEdited(); // B4: this screen's folder list and the shell's ask again
    if (goTo) {
      show({ ...library, folderId: goTo.folderId, subfolders: goTo.folderId !== null && library.subfolders });
      headingRef.current?.focus();
    }
  }

  // D12: a document's name dropped on a folder in the tree moves it there.
  // Said in the toast, with Undo, like the Move dialog: in All documents the row stays where it
  // is, so a message only for screen readers looked like nothing happened (16 Oct 2026).
  function dropDocument(documentId: string, folderId: string | null, placeName: string) {
    const item = shown?.list.items.find((each) => each.id === documentId);
    const name = item?.display_name ?? "The document";
    setDropError(null);
    updateDocument(documentId, { folder_id: folderId }).then(
      () =>
        afterSave(
          `Moved ${name} to ${placeName}.`,
          item
            ? async () => {
                await updateDocument(documentId, { folder_id: item.folder_id });
              }
            : undefined,
        ),
      (caught: unknown) => setDropError(libraryErrorText(caught, "document")),
    );
  }

  function navigated(placeName: string) {
    setAnnouncement(`Showing ${placeName}.`);
  }

  const list = shown?.list;
  // #542: Trash's folders, while no search or filter narrows the files (they are not files).
  // shortcut: all of them on the first page, not paged with the files.
  const trashShown = trashFolders && trashFolders.view === viewKey ? trashFolders : null;
  const opened = trashShown?.opened ?? null;
  const folderRows = trashShown && listQ === "" && !filtered && offset === 0 ? trashShown.items : [];
  const foldersFailed = trashShown?.failed ?? null;
  // Trash is empty only once its folders have come too; until then it is still loading.
  const total = list && (!library.trash || trashShown !== null) ? list.total + folderRows.length : null;
  const view = listView(total, error !== null, listQ, filtered);
  const pages = list ? pageInfo(offset, list.items.length, list.total) : null;
  // Search and filters stay while the next list loads, so an open filter panel stays open.
  const showControls = view === "list" || view === "no-match" || (loaded !== null && !shown);
  const placeName = library.trash
    ? (opened?.folder.name ?? (trashOpen === null ? "Trash" : "Folder in Trash"))
    : (current?.folder.name ??
      (library.folderId !== null
        ? "Folder"
        : library.departmentId === NO_DEPARTMENT
          ? FILES_TEXT.notInDepartment // the tree's Other (issue 409)
          : "All documents"));
  // Nothing is uploaded into Trash, and it has no folder to change.
  const canUpload = !folderGone && !library.trash;
  const withSubfolders = includeSubfoldersHref(library, current?.children.length ?? 0);

  // Only an empty list shows this button, in its empty state; the page header never does
  // (Tushar, 16 Oct 2026: two upload buttons): the app header's Upload is the screen's one
  // primary (G5), and opens #upload-input too.
  // The words are one span: "Upload" and " files" as two flex items got the gap and the space.
  const uploadButton = (
    <button type="button" className="btn" onClick={openPicker}>
      <Icon name="upload" />
      <span>
        Upload<span className="narrow-hide"> files</span>
      </span>
    </button>
  );
  // D1: until the limits come, a skeleton the select's size holds its place. It sits in the
  // list's head row, at the content's right edge (Tushar, 16 Oct 2026: not above the folders),
  // and its choice goes with every upload: the app header's Upload, this screen's, a drop.
  const readerSelect = uploads.limitsLoaded ? (
    <ReaderSelect
      readers={uploads.limits.readers}
      picked={uploads.reader}
      onPick={uploads.chooseReader}
    />
  ) : (
    <span className={`skeleton ${styles.readerSkeleton}`} aria-hidden="true" />
  );
  // PRD 7 PR 11c (#604): All documents empty for someone the walls keep files from; Upload stays.
  const askAccess = library.folderId === null && library.departmentId === null && asksForAccess(me);

  return (
    <Dropzone
      variant="page"
      limits={uploads.limits}
      onFiles={addFiles}
      onFolders={dropFolders}
      enabled={canUpload && view !== "empty"}
      className={`page ${libraryStyles.libraryPage}`}
    >
      <header className="page-header">
        <div>
          <h1>Documents</h1>
          <p>Upload documents, file them in folders, then ask about them.</p>
        </div>
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
      <label htmlFor="sort-input" className="visually-hidden">
        Files for Kaagaz to sort
      </label>
      <input
        ref={sortInputRef}
        id="sort-input"
        className="visually-hidden"
        type="file"
        multiple
        accept={ACCEPT_ATTRIBUTE}
        tabIndex={-1}
        onChange={onChooseToSort}
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
          <FolderNav
            tree={tree}
            library={library}
            loading={folders === null && foldersError === null}
            error={foldersError}
            onRetry={() => setFoldersVersion((n) => n + 1)}
            onNavigate={navigated}
            onDropDocument={canUpload ? dropDocument : undefined}
          />
        </nav>

        <section className={libraryStyles.main} aria-labelledby="place-heading">
          <div className={libraryStyles.placeHead}>
            <div className={libraryStyles.place}>
              {current && <FolderCrumbs tree={tree} library={library} folderId={current.folder.id} />}
              {opened && <TrashCrumbs path={opened.path} onOpen={openTrashFolder} />}
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
              {library.trash && opened === null && (
                <p className={libraryStyles.placeHint}>
                  Documents in Trash are left out of search and chat. Restore one to use it again.
                </p>
              )}
              {opened && (
                <p className={libraryStyles.placeHint}>
                  Restore single files here, or the whole folder from Trash.
                </p>
              )}
              {library.trash && foreverNeeds(me) === "admin_sign_in" && (
                <p className={libraryStyles.placeHint}>
                  To delete for good, <Link href="/login">sign in again</Link>.
                </p>
              )}
              {library.trash && foreverNeeds(me) === "not_allowed" && (
                <p className={libraryStyles.placeHint}>
                  Only admins, and people an admin allows, can delete documents for good.
                </p>
              )}
            </div>
            {canUpload && (
              <div className={styles.uploadControls}>
                {/* F2: a folder being viewed decides where files go; outside one, the department. */}
                {library.folderId === null && (
                  <DepartmentPicker departments={departments} value={department} onChange={pickDepartment} />
                )}
                {/* #484: a whole folder, or loose files for Kaagaz to sort; Read with stays last. */}
                <button type="button" className="btn btn-quiet" onClick={() => folderPicking.pick(library.folderId)}>
                  Upload a folder
                </button>
                {uploads.limits.sort_available && (
                  <button type="button" className="btn btn-quiet" onClick={() => sortInputRef.current?.click()}>
                    Let Kaagaz sort files
                  </button>
                )}
                {readerSelect}
              </div>
            )}
            {folders !== null && canUpload && (
              <FolderActions
                key={current?.folder.id ?? "all"}
                tree={tree}
                current={current}
                onChanged={afterFolderChange}
                admin={me?.user.role === "admin"}
                askNew={newFolderAsk}
                suggestions={nameSuggestions}
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
              {foldersFailed && (
                <div className={styles.errorBanner} role="alert">
                  <Icon name="alert" className={styles.errorIcon} />
                  <div className={styles.errorBody}>
                    <p className={styles.errorTitle}>
                      {trashOpen === null ? "Could not load the folders in Trash." : "Could not load this folder in Trash."}
                    </p>
                    <p>{foldersFailed}</p>
                  </div>
                  <button type="button" className="btn" onClick={() => setReload((n) => n + 1)}>
                    <Icon name="refresh" />
                    Try again
                  </button>
                </div>
              )}
              {library.trash ? (
                view === "empty" &&
                !foldersFailed &&
                <EmptyState icon={opened ? "file" : "trash"} title={trashEmpty(opened !== null).title} headingLevel={3}>
                  {trashEmpty(opened !== null).text}
                </EmptyState>
              ) : view === "empty" ? (
                <Dropzone
                  variant="full"
                  limits={uploads.limits}
                  onFiles={addFiles}
                  onFolders={dropFolders}
                >
                  <EmptyState
                    icon="file"
                    title={current ? "No documents in this folder yet" : askAccess ? ASK_FOR_ACCESS.title : "No documents yet"}
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
                      : askAccess
                        ? ASK_FOR_ACCESS.text
                        : "Upload invoices, registers or bank statements, as PDF, photo or spreadsheet."}
                  </EmptyState>
                </Dropzone>
              ) : null}

              {/* F5: inside a folder, its subfolders first, as tiles with their file counts. */}
              {current && !library.trash && <FolderTiles folders={current.children} library={library} />}

              {view !== "empty" && (
                <div className={styles.list}>
                  {showControls && (
                    <div className={styles.listHead}>
                      {/* desktop/Documents.html: one pill to search in, Search inside its end. */}
                      <form className={styles.search} role="search" onSubmit={submitSearch}>
                        <label htmlFor="documents-q" className={styles.searchLabel}>
                          {mode === "inside" ? "Search inside documents" : "Search documents by name"}
                        </label>
                        <div className={styles.searchField}>
                          <Icon name="search" size={16} className={styles.searchIcon} />
                          <input
                            id="documents-q"
                            className={styles.searchInput}
                            type="search"
                            enterKeyHint="search"
                            autoComplete="off"
                            maxLength={MAX_SEARCH_LENGTH}
                            placeholder={mode === "inside" ? "e.g. rent paid in September" : "e.g. Sharma Industries invoice"}
                            ref={searchRef}
                            key={heardQ}
                            defaultValue={q}
                          />
                          {/* D6: clearing is an x inside the box; the box and Search share one height. */}
                          {q !== "" && (
                            <IconButton icon="close" label="Clear search" className={styles.searchClear} onClick={clearSearch} />
                          )}
                          <button type="submit" className={`k-btn k-btn--primary ${styles.searchGo}`}>
                            Search
                          </button>
                          {/* SoW 19 Oct: where to search, the order and List or Grid, in one menu at
                              the search bar's end (a native popover: Esc or a click outside closes it). */}
                          <button
                            type="button"
                            className={styles.more}
                            popoverTarget="documents-view-options"
                            aria-haspopup="dialog"
                            title="View options"
                          >
                            <Icon name="more" size={18} />
                            <span className="visually-hidden">View options</span>
                          </button>
                        </div>
                      </form>
                      <div
                        id="documents-view-options"
                        popover="auto"
                        role="dialog"
                        aria-label="View options"
                        className={`motion-pop ${styles.morePanel}`}
                      >
                        {!library.trash && (
                          <div className={styles.moreGroup}>
                            <span className={styles.moreLabel}>Search in</span>
                            <RadioPill<SearchMode>
                              label="Search in"
                              name="documents-search-mode"
                              options={SEARCH_MODES.map((choice) => ({ value: choice.mode, label: choice.label }))}
                              value={mode}
                              onChange={setModeChoice}
                            />
                          </div>
                        )}
                        <div className={styles.moreGroup}>
                          <label htmlFor="documents-sort" className={styles.moreLabel}>
                            {FILES_TEXT.sortLabel}
                          </label>
                          <span className="k-select-wrap">
                            <select
                              id="documents-sort"
                              className={`k-select ${styles.sortSelect}`}
                              value={library.sort}
                              onChange={(event) => sortBy(event.target.value as SortKey)}
                            >
                              {SORT_KEYS.map((key) => (
                                <option key={key} value={key}>
                                  {FILES_TEXT.sortNames[key]}
                                </option>
                              ))}
                            </select>
                            <KitIcon name="chev-down" size="sm" className="k-ic" />
                          </span>
                        </div>
                        {/* F5: List or Grid, remembered per person. */}
                        <div className={styles.moreGroup}>
                          <span className={styles.moreLabel}>{FILES_TEXT.showAs}</span>
                          <Segmented
                            label={FILES_TEXT.showAs}
                            options={[
                              { value: "list", label: FILES_TEXT.list, icon: "list" },
                              { value: "grid", label: FILES_TEXT.grid, icon: "grid" },
                            ]}
                            value={viewMode}
                            onChange={chooseView}
                          />
                        </div>
                      </div>
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
                      departments={departments}
                      people={people}
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
                      <p className={styles.errorBody}>The list did not refresh, so it may be out of date.</p>
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
                      onSearchNames={() => setModeChoice("names")}
                    />
                  )}

                  {/* #542: Trash's folders are rows of the list, so Trash shows as a list. */}
                  {searchKey === null && shown && view === "list" && shown.list.items.length > 0 && viewMode === "grid" && folderRows.length === 0 && (
                    <DocumentsGrid items={shown.list.items} label={library.trash ? "Documents in Trash" : "Your documents"} />
                  )}

                  {searchKey === null && shown && view === "list" && (shown.list.items.length > 0 || folderRows.length > 0) && (viewMode === "list" || folderRows.length > 0) && (
                    <DocumentsTable
                      sorting={{ sort: library.sort, order: library.order, onSort: sortBy }}
                      items={shown.list.items}
                      me={me}
                      now={shown.now}
                      tree={tree}
                      tags={tags}
                      inTrash={library.trash}
                      suggestions={nameSuggestions}
                      movedLeaves={library.folderId !== null}
                      onFolderCreated={foldersEdited}
                      onRemoved={afterRemoved}
                      onSaved={afterSave}
                      onFocusList={() => headingRef.current?.focus()}
                      onTagCreated={() => setTagsVersion((n) => n + 1)}
                      onUploadVersion={chooseVersion}
                      trashFolders={folderRows}
                      onOpenFolder={(folder) => openTrashFolder(folder.id)}
                      onFolderDone={opened ? undefined : afterTrashFolder}
                    />
                  )}

                  {searchKey === null && pages && list && list.total > 0 && view === "list" && (
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

      <Toast
        message={toast?.message ?? null}
        onUndo={toastUndo ? () => undoMove(toastUndo, toast?.undone ?? "") : undefined}
        onClose={() => setToast(null)}
      />
      {versionFile && (
        <VersionNoteDialog
          documentName={versionFile.of.display_name}
          fileName={versionFile.file.name}
          onUpload={uploadVersionWith}
          onClose={() => setVersionFile(null)}
        />
      )}
    </Dropzone>
  );
}
