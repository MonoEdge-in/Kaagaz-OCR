"use client";

// One upload queue and one batch tray for every page (docs/architecture.md, Decisions: D-12): the
// shell holds them, so a drop keeps going and stays in view while the person moves
// between screens. The documents screen and the dashboard add files to this queue
// (into the folder or department they show) and hear when files are stored, to
// refresh. Everywhere else the top bar's Upload opens this file picker, into the last
// department used (or none: Kaagaz files it). The tray is the status bar's
// UploadStatus (components/documents/UploadArea.tsx).
// Upload a folder (#484) lives here too, so it keeps going between screens: the folder picker
// (<input webkitdirectory>), a dropped folder read one at a time, the dialog before Start
// (FolderUploadDialog), and each folder upload's row in the tray (FolderUploadRow).

import { createContext, useCallback, useContext, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import { NO_FOLDER_PICKER, buildManifest, fromFileList, noFolderPaths, walkDrop } from "@/components/documents/folderPick";
import type { FolderSource } from "@/components/documents/folderUpload";
import { FolderReadingDialog, FolderUploadDialog } from "@/components/documents/FolderUploadDialog";
import { FolderUploadRow } from "@/components/documents/FolderUploadRow";
import { buildTree } from "@/components/documents/folderTree";
import { useFolderUploads } from "@/components/documents/useFolderUploads";
import { ACCEPT_ATTRIBUTE } from "@/components/documents/logic";
import { UploadStatus, useUploads } from "@/components/documents/UploadArea";
import { NOTIFY_MS } from "@/components/documents/uploadQueue";
import { departmentsOf, lastDepartment } from "@/components/files/departments";
import { canReview } from "@/components/review/review";
import { listFolders } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Folder } from "@/lib/types";

/** The shell's own file input; a screen with its own (#upload-input) is used first. */
export const SHELL_UPLOAD_INPUT_ID = "upload-input-shell";
const FOLDER_INPUT_ID = "folder-input-shell";

type Queue = ReturnType<typeof useUploads>;

/** Upload a folder (#484): pick one, or take dropped ones; it goes inside folderId at first. */
interface FolderPicking {
  pick(folderId: string | null): void;
  drop(entries: FileSystemDirectoryEntry[], folderId: string | null): void;
}

interface SharedUploads {
  uploads: Queue;
  trayOpen: boolean;
  setTrayOpen(open: boolean): void;
  listen(listener: () => void): () => void;
  folders: FolderPicking;
}

/** The step before a folder upload starts: a dropped folder still being read, or read. */
type Pending =
  | { kind: "reading"; name: string; found: number; folderId: string | null; failed?: boolean | string }
  | { kind: "ready"; source: FolderSource; folderId: string | null };

const UploadsContext = createContext<SharedUploads | null>(null);

export function UploadsProvider({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const listeners = useRef(new Set<() => void>());
  const notifyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // One refresh for a run of stored files: a 100-file drop asks each screen again at most
  // every NOTIFY_MS (3 s) while it lands, not 100 times in a row.
  const notify = () => {
    if (notifyTimer.current !== null) return;
    notifyTimer.current = setTimeout(() => {
      notifyTimer.current = null;
      listeners.current.forEach((listener) => listener());
    }, NOTIFY_MS);
  };
  useEffect(
    () => () => {
      if (notifyTimer.current !== null) clearTimeout(notifyTimer.current);
    },
    [],
  );
  const uploads = useUploads(notify);
  const [trayOpen, setTrayOpen] = useState(false);
  const [folders, setFolders] = useState<Folder[]>([]);
  const tree = useMemo(() => buildTree(folders), [folders]);

  // The folders, for "Filed into Invoices" in the tray and the department a file goes
  // into; asked again as each drop settles, since Kaagaz may have made a folder for it.
  useEffect(() => {
    let cancelled = false;
    listFolders().then(
      (reply) => {
        if (!cancelled) setFolders(reply.items);
      },
      () => undefined, // the tray says "Filed" without the folder's name
    );
    return () => {
      cancelled = true;
    };
  }, [uploads.settled]);

  const listen = useCallback((listener: () => void) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);
  const folderUploads = useFolderUploads({ userId: me.user.id, limits: uploads.limits, reading: uploads.reading, onStored: notify });
  const folderInput = useRef<HTMLInputElement | null>(null);
  const pickFor = useRef<{ folderId: string | null; again: string | null }>({ folderId: null, again: null });
  const dropQueue = useRef<{ entries: FileSystemDirectoryEntry[]; folderId: string | null }>({ entries: [], folderId: null });
  const readCount = useRef(0); // a newer read, or Cancel, makes an older read's result go unused
  const [pending, setPending] = useState<Pending | null>(null);

  function readNextDrop() {
    const entry = dropQueue.current.entries.shift();
    if (!entry) return;
    const { folderId } = dropQueue.current;
    const read = ++readCount.current;
    setPending({ kind: "reading", name: entry.name, found: 0, folderId });
    walkDrop(entry, (found) => {
      if (read === readCount.current) setPending({ kind: "reading", name: entry.name, found, folderId });
    }).then(
      (picked) => {
        if (read === readCount.current) setPending({ kind: "ready", source: { picked, manifest: buildManifest(picked) }, folderId });
      },
      () => {
        if (read === readCount.current) setPending({ kind: "reading", name: entry.name, found: 0, folderId, failed: true });
      },
    );
  }

  function closePending() {
    readCount.current += 1;
    setPending(null);
    readNextDrop(); // several folders dropped at once: each its own upload, one after another
  }

  const folderPicking: FolderPicking = {
    pick: (folderId) => {
      pickFor.current = { folderId, again: null };
      folderInput.current?.click();
    },
    drop: (entries, folderId) => {
      dropQueue.current = { entries: [...dropQueue.current.entries, ...entries], folderId };
      if (pending === null) readNextDrop();
    },
  };

  function onFolderChosen(event: ChangeEvent<HTMLInputElement>) {
    const files = event.target.files ?? [];
    const { folderId, again } = pickFor.current;
    if (noFolderPaths(files)) {
      event.target.value = "";
      setPending({ kind: "reading", name: "", found: 0, folderId, failed: NO_FOLDER_PICKER });
      return;
    }
    const picked = fromFileList(files);
    event.target.value = "";
    if (!picked) return;
    const source = { picked, manifest: buildManifest(picked) };
    if (again !== null) folderUploads.resume(again, source);
    else setPending({ kind: "ready", source, folderId });
  }

  // useUploads gives a new queue object each render, so this is new each render too.
  const shared: SharedUploads = { uploads, trayOpen, setTrayOpen, listen, folders: folderPicking };

  function onChoose(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;
    setTrayOpen(true);
    void uploads.addFiles(files, undefined, lastDepartment(departmentsOf(folders)) ?? undefined);
  }

  return (
    <UploadsContext.Provider value={shared}>
      {children}
      <label htmlFor={SHELL_UPLOAD_INPUT_ID} className="visually-hidden">
        Files to upload
      </label>
      <input
        id={SHELL_UPLOAD_INPUT_ID}
        className="visually-hidden"
        type="file"
        multiple
        accept={ACCEPT_ATTRIBUTE}
        tabIndex={-1}
        onChange={onChoose}
      />
      <label htmlFor={FOLDER_INPUT_ID} className="visually-hidden">
        Folder to upload
      </label>
      <input
        id={FOLDER_INPUT_ID}
        ref={(element) => {
          folderInput.current = element;
          if (element) element.webkitdirectory = true; // not a React prop: a folder, with every file inside it
        }}
        className="visually-hidden"
        type="file"
        multiple
        tabIndex={-1}
        onChange={onFolderChosen}
      />
      {pending?.kind === "reading" && (
        <FolderReadingDialog name={pending.name} found={pending.found} failed={pending.failed} onClose={closePending} />
      )}
      {pending?.kind === "ready" && (
        <FolderUploadDialog
          source={pending.source}
          tree={tree}
          folderId={pending.folderId}
          admin={me.user.role === "admin"}
          limits={uploads.limits.folder_upload}
          onStart={(target, merge) => {
            folderUploads.start(pending.source, target, merge);
            setTrayOpen(true);
            closePending();
          }}
          onClose={closePending}
        />
      )}
      <UploadStatus
        folderText={folderUploads.views.map((view) => view.spoken).join(" · ")}
        folderRows={
          folderUploads.views.length === 0
            ? null
            : folderUploads.views.map((view) => (
                <FolderUploadRow
                  key={view.id}
                  view={view}
                  held={folderUploads.held.has(view.id)}
                  notSent={folderUploads.notSentOf(view.id)}
                  tree={tree}
                  limits={uploads.limits}
                  actions={{
                    stop: () => folderUploads.stop(view.id),
                    resume: () => folderUploads.resume(view.id),
                    chooseAgain: () => {
                      pickFor.current = { folderId: null, again: view.id };
                      folderInput.current?.click();
                    },
                    retry: (itemId) => folderUploads.retry(view.id, [itemId]),
                    retryAll: () => folderUploads.retry(view.id),
                    clear: () => folderUploads.clear(view.id),
                    loadItems: (kind, after) => folderUploads.loadItems(view.id, kind, after),
                  }}
                />
              ))
        }
        open={trayOpen}
        onOpenChange={setTrayOpen}
        entries={uploads.entries}
        views={uploads.views}
        stored={uploads.stored}
        checkFailing={uploads.checkFailing}
        onClearFinished={uploads.clearFinished}
        onRestore={(key, documentId, name) => void uploads.restore(key, documentId, name)}
        onRetry={(key) => void uploads.retry(key)}
        tree={tree}
        onUndone={notify}
        canReread={canReview(me.user.role)}
      />
    </UploadsContext.Provider>
  );
}

/**
 * The shell's upload queue, for a screen that adds files to it. `onUploaded` runs each
 * time a file is stored or an auto-filing is undone, so the screen can ask again.
 */
export function useSharedUploads(onUploaded: () => void): SharedUploads {
  const shared = useContext(UploadsContext);
  if (shared === null) throw new Error("useSharedUploads needs the app shell's UploadsProvider");
  const heard = useEffectEvent(onUploaded);
  const { listen } = shared;
  useEffect(() => listen(() => heard()), [listen]);
  return shared;
}
