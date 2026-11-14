"use client";

// Page turns saved for everyone (#600). A file opens with its saved turns, and then this browser's
// own turns of it (#589) are dropped; a file with none saved opens with this browser's own, still
// kept here as they change. A choice in the turn menu stays on this screen only until "Save for
// everyone": every save sends only the pages whose turn differs from the saved one (turn 0 for a
// page put back), so a save never takes away a turn someone else saved on another page (the lead's
// decision on the review: there is no whole-file write). A save that leaves nothing turned asks
// first. The button stays in place, disabled, while there is nothing to save, so keyboard focus is
// not lost. A failed save leaves the turns on the screen and says why, until the turns change.

import { useCallback, useEffect, useState } from "react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ApiError, lostConnection, savePageTurnsForEveryone } from "@/lib/api";
import type { PageTurnSaved } from "@/lib/types";
import { fromServer, openingTurns, pageTurnChanges, readPageTurns, savePageTurns } from "./viewer";

type Turns = ReadonlyMap<number, number>;

export const ONLY_HERE = "Your turns are only on this computer until you save them.";
export const SAVED = "Saved. Everyone now sees these pages turned.";
export const PUT_BACK = "Saved. Everyone now sees these pages as scanned.";
export const OFFLINE = "Not saved. Check your connection, then try again.";
export const ASK_PUT_BACK = "Put these pages back as they were scanned, for everyone?";

/** The file's saved turns, the turns on this screen (page index to quarter turns right), and
 * whether a save is on its way (the turn menus wait for it). */
export function usePageTurns(documentId: string, pageTurns: readonly PageTurnSaved[] | undefined, userId: string) {
  const [saved, setSaved] = useState<Turns>(() => fromServer(pageTurns ?? [])); // none from an older server
  const [opening] = useState(() => openingTurns(saved, readPageTurns(userId, documentId)));
  const [shown, setShown] = useState<Turns>(opening);
  const [saving, setSaving] = useState(false);
  const anySaved = saved.size > 0;
  useEffect(() => {
    if (anySaved) savePageTurns(userId, documentId, new Map()); // saved turns drop this browser's own
  }, [anySaved, userId, documentId]);
  const show = useCallback(
    (turns: Turns) => {
      setShown(turns);
      if (!anySaved) savePageTurns(userId, documentId, turns);
    },
    [anySaved, userId, documentId],
  );
  /** A save's reply: the saved set, others' turns included, is now what shows. */
  const takeSaved = useCallback((next: Turns) => {
    setSaved(next);
    setShown(next);
  }, []);
  return { saved, takeSaved, opening, shown, show, saving, setSaving };
}

export type PageTurnsState = ReturnType<typeof usePageTurns>;

export function SaveForEveryone({
  documentId,
  userId,
  turns,
  onSaved,
  className,
}: {
  documentId: string;
  userId: string;
  turns: PageTurnsState;
  /** After a save, with the whole saved set (others' turns included): the screen reloads its detail. */
  onSaved?: (saved: Turns) => void;
  className?: string;
}) {
  const { saved, shown, saving, setSaving, takeSaved } = turns;
  const [asking, setAsking] = useState(false);
  // A failure is said until the turns change; a success is announced once.
  const [outcome, setOutcome] = useState<{ text: string; failed: boolean; shown: Turns } | null>(null);
  const said = outcome && (!outcome.failed || outcome.shown === shown) ? outcome : null;
  const changes = pageTurnChanges(saved, shown);
  const backAsScanned = shown.size === 0 && saved.size > 0;

  const send = async () => {
    setSaving(true);
    try {
      const reply = await savePageTurnsForEveryone(documentId, changes, userId);
      const next = fromServer(reply.page_turns);
      takeSaved(next);
      onSaved?.(next);
      setOutcome({ text: backAsScanned ? PUT_BACK : SAVED, failed: false, shown });
    } catch (caught) {
      const text = caught instanceof ApiError && !lostConnection(caught) ? caught.message : OFFLINE;
      setOutcome({ text, failed: true, shown });
    } finally {
      setSaving(false);
      setAsking(false);
    }
  };

  return (
    <>
      <button
        type="button"
        className={className}
        title={changes.length > 0 ? ONLY_HERE : undefined}
        disabled={saving || changes.length === 0}
        onClick={() => (backAsScanned ? setAsking(true) : void send())}
      >
        {backAsScanned ? "Save for everyone: back as scanned" : "Save for everyone"}
      </button>
      <span role="status" className={said?.failed ? undefined : "visually-hidden"}>
        {said?.text}
      </span>
      <ConfirmDialog
        open={asking}
        title={ASK_PUT_BACK}
        confirmLabel="Put back"
        busy={saving}
        onConfirm={() => void send()}
        onCancel={() => setAsking(false)}
      >
        {null}
      </ConfirmDialog>
    </>
  );
}
