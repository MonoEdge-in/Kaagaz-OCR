"use client";

// One upload queue and one batch tray for every page (docs/architecture.md, Decisions: D-12): the
// shell holds them, so a drop keeps going and stays in view while the person moves
// between screens. The documents screen and the dashboard add files to this queue
// (into the folder or department they show) and hear when files are stored, to
// refresh. Everywhere else the top bar's Upload opens this file picker, into the last
// department used (or none: Kaagaz files it). The tray is the status bar's
// UploadStatus (components/documents/UploadArea.tsx).

import { createContext, useCallback, useContext, useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import { buildTree } from "@/components/documents/folderTree";
import { ACCEPT_ATTRIBUTE } from "@/components/documents/logic";
import { UploadStatus, useUploads } from "@/components/documents/UploadArea";
import { departmentsOf, lastDepartment } from "@/components/files/departments";
import { canReview } from "@/components/review/review";
import { listFolders } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Folder } from "@/lib/types";

/** How long the screens wait to hear of stored files, so a run of them is one refresh. */
const NOTIFY_MS = 1000;

/** The shell's own file input; a screen with its own (#upload-input) is used first. */
export const SHELL_UPLOAD_INPUT_ID = "upload-input-shell";

type Queue = ReturnType<typeof useUploads>;

interface SharedUploads {
  uploads: Queue;
  trayOpen: boolean;
  setTrayOpen(open: boolean): void;
  listen(listener: () => void): () => void;
}

const UploadsContext = createContext<SharedUploads | null>(null);

export function UploadsProvider({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const listeners = useRef(new Set<() => void>());
  const notifyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // One refresh for a run of stored files: a 100-file drop asks each screen again about once
  // a second while it lands, not 100 times in a row.
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
  const shared = useMemo<SharedUploads>(() => ({ uploads, trayOpen, setTrayOpen, listen }), [uploads, trayOpen, listen]);

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
      <UploadStatus
        open={trayOpen}
        onOpenChange={setTrayOpen}
        entries={uploads.entries}
        views={uploads.views}
        stored={uploads.stored}
        tooMany={uploads.tooMany}
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
