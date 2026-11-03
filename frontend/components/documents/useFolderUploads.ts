"use client";

// The folder uploads of this tab (#484), held by the app shell beside the file queue
// (components/shell/Uploads.tsx), so an upload keeps going while the person moves between
// screens. State is counts only (folderUpload.ts); the picked files, the files not sent and the
// Stop marks are in refs, never copied per event. After a reload, the server's folder uploads
// with files still waiting come back as "Choose “A” again" (the list has no reading counts, so
// one only being read is not brought back). One finished in this tab is followed until it is read.

import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import {
  UPLOAD_CALLS_PER_MINUTE,
  createTurn,
  getFolderUpload,
  listFolderItems,
  listFolderUploads,
  planFolderUpload,
  registerFolderItems,
  uploadFolderItem,
} from "@/lib/api";
import { POLL_STEPS_MS } from "@/lib/poll";
import type { UploadLimits, UploadReading } from "@/lib/types";
import { buildManifest } from "./folderPick";
import { EMPTY_FOLDER_UPLOADS, folderUploadsReducer, sendWaiting, startFolderUpload } from "./folderUpload";
import type { FolderEvent, FolderSource, FolderTarget, FolderUploadApi } from "./folderUpload";
import type { NotSentRow } from "./FolderUploadRow";

/** The files not sent shown on a row; the rest are counted. */
const NOT_SENT_SHOWN = 20;

export function useFolderUploads({
  userId,
  limits,
  reading,
  onStored,
}: {
  userId: string;
  limits: UploadLimits;
  reading: UploadReading | undefined;
  onStored(): void;
}) {
  const [state, dispatch] = useReducer(folderUploadsReducer, EMPTY_FOLDER_UPLOADS);
  const sources = useRef(new Map<string, FolderSource>());
  const stops = useRef(new Set<string>());
  const notSent = useRef(new Map<string, Map<string, NotSentRow>>());
  const listed = useRef(new Set<string>()); // registration finished in this tab
  const starts = useRef(new Map<string, { target: FolderTarget; merge: boolean }>()); // to list again
  // What the rows show, copied from the refs after each change: at most NOT_SENT_SHOWN rows each.
  const [held, setHeld] = useState<ReadonlySet<string>>(new Set());
  const [notSentShown, setNotSentShown] = useState<Readonly<Record<string, { rows: NotSentRow[]; total: number }>>>({});
  const [pollRound, setPollRound] = useState(0);
  // The folder upload's own pace: 90% of what the server takes, so it is never refused for it.
  const perMinute = limits.folder_upload?.calls_per_minute ?? UPLOAD_CALLS_PER_MINUTE;
  const turn = useMemo(() => createTurn(Math.max(1, Math.floor(perMinute * 0.9))), [perMinute]);
  const atOnce = limits.folder_upload?.files_at_once;

  const api: FolderUploadApi = {
    plan: planFolderUpload,
    register: async (id, user, items) => {
      await registerFolderItems(id, user, items);
    },
    items: listFolderItems,
    send: (file, item, onProgress) => uploadFolderItem(file, item.id, userId, onProgress, turn, reading),
    summary: getFolderUpload,
  };

  const rowsOf = (id: string) => {
    let rows = notSent.current.get(id);
    if (!rows) notSent.current.set(id, (rows = new Map()));
    return rows;
  };
  const showNotSent = (id: string) => {
    const rows = rowsOf(id);
    setNotSentShown((shown) => ({ ...shown, [id]: { rows: [...rows.values()].slice(0, NOT_SENT_SHOWN), total: rows.size } }));
  };
  const hold = (id: string, source: FolderSource | null) => {
    if (source) sources.current.set(id, source);
    else sources.current.delete(id);
    setHeld(new Set(sources.current.keys()));
  };

  function emit(event: FolderEvent) {
    if (event.type === "notSent") {
      rowsOf(event.id).set(event.itemId, { item: event.item, message: event.message, retry: event.retry });
      showNotSent(event.id);
    }
    if (event.type === "phase" && event.phase === "sending") listed.current.add(event.id);
    if (event.type === "sent" && event.outcome !== "failed") onStored();
    dispatch(event);
  }

  const stopped = (id: string) => () => stops.current.has(id);

  function start(source: FolderSource, target: FolderTarget, merge: boolean) {
    const id = crypto.randomUUID();
    hold(id, source);
    starts.current.set(id, { target, merge });
    void startFolderUpload(api, { id, userId, source, target, merge, limits }, emit, stopped(id));
  }

  function stop(id: string) {
    stops.current.add(id);
  }

  /** Send what is still waiting, with the files this tab holds or the folder picked again. */
  function resume(id: string, picked?: FolderSource) {
    const view = state[id];
    // After a reload the row has Kaagaz's name, "A (2)" when kept separate: "A" is that folder.
    const same = (name: string) => view?.rootName === name || view?.rootName.match(/^(.*) \(\d+\)$/)?.[1] === name;
    if (picked && view && !same(picked.picked.rootName)) {
      const message = `This is “${picked.picked.rootName}”. Choose “${view.rootName}” to continue.`;
      dispatch({ type: "phase", id, phase: view.phase, message });
      return;
    }
    // Listed part way: the re-plan needs the first plan's folders first, in their order.
    const earlier = sources.current.get(id)?.manifest;
    if (picked && earlier && !listed.current.has(id)) {
      hold(id, { picked: picked.picked, manifest: buildManifest(picked.picked, earlier) });
    } else if (picked) hold(id, picked);
    const source = sources.current.get(id);
    if (!source) return;
    stops.current.delete(id);
    const rows = rowsOf(id);
    dispatch({ type: "retrying", id, count: rows.size });
    rows.clear();
    showNotSent(id);
    // Listing stopped part way in this tab: start again with the same id, which finds the same
    // folders (the server's re-plan of one id) and gives back the files already listed.
    const again = starts.current.get(id);
    if (!listed.current.has(id) && again) {
      void startFolderUpload(api, { id, userId, source, ...again, limits }, emit, stopped(id));
    } else {
      void sendWaiting(api, { id, userId, source, atOnce }, emit, stopped(id));
    }
  }

  function retry(id: string, itemIds?: readonly string[]) {
    const source = sources.current.get(id);
    const rows = rowsOf(id);
    const chosen = [...rows.values()].filter((row) => row.retry && (!itemIds || itemIds.includes(row.item.id)));
    if (!source || chosen.length === 0) return;
    for (const row of chosen) rows.delete(row.item.id);
    dispatch({ type: "retrying", id, count: chosen.length });
    showNotSent(id);
    stops.current.delete(id);
    void sendWaiting(api, { id, userId, source, only: chosen.map((row) => row.item), atOnce }, emit, stopped(id));
  }

  function clear(id: string) {
    hold(id, null);
    notSent.current.delete(id);
    setNotSentShown((shown) => {
      const rest = { ...shown };
      delete rest[id];
      return rest;
    });
    dispatch({ type: "remove", id });
  }

  // After a reload: the folder uploads with files still waiting, or still being read.
  useEffect(() => {
    let cancelled = false;
    listFolderUploads().then(
      ({ items }) => {
        if (cancelled) return;
        for (const summary of items) {
          if (summary.waiting === 0) continue;
          dispatch({ type: "start", id: summary.id, rootName: summary.root_name, total: summary.file_count });
          dispatch({ type: "counts", id: summary.id, summary });
          dispatch({ type: "phase", id: summary.id, phase: "needs_pick" });
        }
      },
      () => undefined, // nothing to bring back; the server keeps every item
    );
    return () => {
      cancelled = true;
    };
  }, []);

  // Follow the reading of each settled upload, one round every POLL_STEPS_MS[1], until it is read.
  const followKey = Object.values(state)
    .filter((view) => view.phase === "done" && view.reading > 0)
    .map((view) => view.id)
    .join(",");
  useEffect(() => {
    if (!followKey) return;
    const timer = setTimeout(() => {
      const asked = followKey.split(",").map((id) =>
        getFolderUpload(id).then(
          (summary) => dispatch({ type: "counts", id, summary }),
          () => undefined, // asked again next round
        ),
      );
      void Promise.all(asked).then(() => setPollRound((n) => n + 1));
    }, POLL_STEPS_MS[1]);
    return () => clearTimeout(timer);
  }, [followKey, pollRound]);

  const views = Object.values(state).reverse(); // newest first
  return {
    views,
    held,
    notSentOf: (id: string) => notSentShown[id] ?? { rows: [], total: 0 },
    start,
    stop,
    resume,
    retry,
    clear,
    loadItems: (id: string, view: "attention" | "duplicate", after: number) => listFolderItems(id, view, after, 500),
    sending: views.some((view) => view.phase === "sending" || view.phase === "registering" || view.phase === "planning"),
  };
}
