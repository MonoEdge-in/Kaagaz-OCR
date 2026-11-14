"use client";

// Page turns saved for everyone (#600). A file opens with its saved turns, and then this browser's
// own turns of it (#589) are dropped; a file with none saved opens with this browser's own, still
// kept here as they change. A choice in the turn menu stays on this screen only until "Save for
// everyone": the save sends only the pages whose turn differs from the saved one, so two people
// turning different pages both keep theirs. With every turn taken off a file that has saved
// turns, the button puts the pages back as scanned for everyone, after asking. A failed save
// leaves the turns on the screen and says why.

import { useCallback, useEffect, useState } from "react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ApiError, lostConnection, resetPageTurnsForEveryone, savePageTurnsForEveryone } from "@/lib/api";
import type { PageTurnSaved } from "@/lib/types";
import { fromServer, openingTurns, pageTurnChanges, readPageTurns, savePageTurns } from "./viewer";

type Turns = ReadonlyMap<number, number>;

export const ONLY_HERE = "Your turns are only on this computer until you save them.";
export const SAVED = "Saved. Everyone now sees these pages turned.";
export const PUT_BACK = "Saved. Everyone now sees the pages as scanned.";
export const OFFLINE = "Not saved. Check your connection, then try again.";

/** The file's saved turns and the turns on this screen (page index to quarter turns right). */
export function usePageTurns(documentId: string, pageTurns: readonly PageTurnSaved[], userId: string) {
  const [saved, setSaved] = useState<Turns>(() => fromServer(pageTurns));
  const [opening] = useState(() => openingTurns(saved, readPageTurns(userId, documentId)));
  const [shown, setShown] = useState<Turns>(opening);
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
  return { saved, takeSaved, opening, shown, show };
}

export function SaveForEveryone({
  documentId,
  userId,
  saved,
  shown,
  onSaved,
  className,
}: {
  documentId: string;
  userId: string;
  saved: Turns;
  shown: Turns;
  /** The whole saved set after the save, others' turns included. */
  onSaved: (saved: Turns) => void;
  className?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [outcome, setOutcome] = useState<{ text: string; failed: boolean } | null>(null);
  const changes = pageTurnChanges(saved, shown);
  const backAsScanned = shown.size === 0 && saved.size > 0;

  const send = async (reset: boolean) => {
    setBusy(true);
    try {
      const reply = reset
        ? await resetPageTurnsForEveryone(documentId, userId)
        : await savePageTurnsForEveryone(documentId, changes, userId);
      onSaved(fromServer(reply.page_turns));
      setOutcome({ text: reset ? PUT_BACK : SAVED, failed: false });
    } catch (caught) {
      const text = caught instanceof ApiError && !lostConnection(caught) ? caught.message : OFFLINE;
      setOutcome({ text, failed: true });
    } finally {
      setBusy(false);
      setAsking(false);
    }
  };

  return (
    <>
      {changes.length > 0 ? (
        <button
          type="button"
          className={className}
          title={ONLY_HERE}
          disabled={busy}
          onClick={() => (backAsScanned ? setAsking(true) : void send(false))}
        >
          {backAsScanned ? "Save for everyone: back as scanned" : "Save for everyone"}
        </button>
      ) : null}
      <span role="status" className={outcome?.failed ? undefined : "visually-hidden"}>
        {outcome?.text}
      </span>
      <ConfirmDialog
        open={asking}
        title="Put every page back as it was scanned, for everyone?"
        confirmLabel="Put back"
        busy={busy}
        onConfirm={() => void send(true)}
        onCancel={() => setAsking(false)}
      >
        {null}
      </ConfirmDialog>
    </>
  );
}
