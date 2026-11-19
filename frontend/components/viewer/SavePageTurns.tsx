"use client";

// Page turns saved for everyone (#600). A file opens with its saved turns, and then this browser's
// own turns of it (#589) are dropped; a file with none saved opens with this browser's own, still
// kept here as they change. A choice in the turn menu stays on this screen only until "Save for
// everyone": every save sends only the pages whose turn differs from the saved one (turn 0 for a
// page put back), so a save never takes away a turn someone else saved on another page (the lead's
// decision on the review: there is no whole-file write). A save that leaves nothing turned asks
// first. The button stays in place, disabled, while there is nothing to save, so keyboard focus is
// not lost. A failed save leaves the turns on the screen and says why, until the turns change.

//
// #709: the turn menus' choices with the orientation check (useTurnWithAi). "Straighten all pages
// with AI" turns every page it could tell upright and saves those pages for everyone; "Make all
// pages wide" and "tall" use the check too and stay on this screen. Each says what it did, with
// Undo, which puts those pages back (and saves that, when they were saved). Nothing turns while
// the check runs, or when it fails.

import { useCallback, useEffect, useState } from "react";
import { turnPagesUpright } from "@/components/extend/lib/page-turns";
import type { PageTurns, TurnablePage } from "@/components/extend/lib/page-turns";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ApiError, lostConnection, savePageTurnsForEveryone, straightenPages } from "@/lib/api";
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

// --- #709: the turn menus' choices with the orientation check ---------------------------------

export type AiTurnChoice = "straighten" | "wide" | "tall";
type Undo = { before: Turns; changed: number[]; saved: boolean };

const NO_CONNECTION = "Check your connection, then try again.";
const pagesWord = (count: number) => `${count} ${count === 1 ? "page" : "pages"}`;
const whyNot = (caught: unknown) =>
  caught instanceof ApiError && !lostConnection(caught) ? caught.message : NO_CONNECTION;

/** What a choice with the check did. saveFailed: undefined when nothing was saved, null when the
 * turned pages were saved for everyone, else why they weren't. */
export function turnedWords(
  choice: AiTurnChoice,
  turned: number,
  unsure: readonly number[],
  saveFailed?: string | null,
): string {
  const said: string[] = [];
  const what = `Turned ${pagesWord(turned)}${choice === "straighten" ? " upright" : ""}`;
  if (turned > 0 && saveFailed === null) said.push(`${what} for everyone.`);
  else if (turned > 0 && saveFailed) said.push(`${what} for you. Couldn't save them for everyone: ${saveFailed}`);
  else if (turned > 0) said.push(`${what}.`);
  else if (unsure.length === 0) said.push(`All pages are already ${choice === "straighten" ? "upright" : choice}.`);
  if (unsure.length === 1) said.push(`Couldn't tell which way up for page ${unsure[0]}.`);
  else if (unsure.length > 5) said.push(`Couldn't tell which way up for ${unsure.length} pages.`);
  else if (unsure.length > 1) {
    said.push(`Couldn't tell which way up for pages ${unsure.slice(0, -1).join(", ")} and ${unsure.at(-1)}.`);
  }
  return said.join(" ");
}

function undoneWords(count: number, saveFailed?: string | null): string {
  const back = count === 1 ? "Put 1 page back as it was" : `Put ${count} pages back as they were`;
  if (saveFailed === null) return `${back}, for everyone.`;
  return saveFailed ? `${back} for you. Couldn't save that for everyone: ${saveFailed}` : `${back}.`;
}

/** The turn menus' choices with the check, for the file's turns: `apply` shows a set of turns in
 * the viewer; `onSaved` after a save for everyone (the screen reloads the file's detail). */
export function useTurnWithAi(
  documentId: string,
  userId: string,
  turns: PageTurnsState,
  apply: (next: Turns) => void,
  onSaved?: () => void,
) {
  const [checking, setChecking] = useState<number | null>(null); // the pages being checked
  const [said, setSaid] = useState<{ text: string; undo: Undo | null } | null>(null);
  const busy = checking !== null || turns.saving;

  /** Saves `shown`'s turn of the `changed` pages for everyone: null when saved, else why not. The
   * reply is the saved set; this screen keeps its own unsaved turns of the other pages. */
  const saveFor = async (changed: number[], shown: Turns): Promise<string | null> => {
    turns.setSaving(true);
    try {
      const reply = await savePageTurnsForEveryone(
        documentId,
        changed.map((index) => ({ page_no: index + 1, turn: ((shown.get(index) ?? 0) * 90) as 0 | 90 | 180 | 270 })),
        userId,
      );
      const saved = fromServer(reply.page_turns);
      const next = new Map(saved);
      for (const index of new Set([...shown.keys(), ...turns.saved.keys()])) {
        const own = shown.get(index) ?? 0;
        if (changed.includes(index) || own === (turns.saved.get(index) ?? 0)) continue;
        if (own) next.set(index, own);
        else next.delete(index);
      }
      turns.takeSaved(saved);
      apply(next);
      onSaved?.();
      return null;
    } catch (caught) {
      return whyNot(caught);
    } finally {
      turns.setSaving(false);
    }
  };

  const run = async (choice: AiTurnChoice, pages: readonly TurnablePage[]) => {
    if (busy) return;
    setSaid(null);
    setChecking(pages.length);
    let checked: Awaited<ReturnType<typeof straightenPages>>;
    try {
      checked = await straightenPages(documentId);
    } catch (caught) {
      setSaid({ text: `Couldn't check the pages. ${whyNot(caught)}`, undo: null });
      return;
    } finally {
      setChecking(null);
    }
    const before = turns.shown;
    const next = turnPagesUpright(before as PageTurns, pages, checked, choice === "straighten" ? undefined : choice);
    const changed = pages
      .map((_, index) => index)
      .filter((index) => (before.get(index) ?? 0) !== (next.get(index) ?? 0));
    apply(next);
    const failed = choice === "straighten" && changed.length > 0 ? await saveFor(changed, next) : undefined;
    setSaid({
      text: turnedWords(choice, changed.length, checked.unsure, failed),
      undo: changed.length > 0 ? { before, changed, saved: failed === null } : null,
    });
  };

  /** Those pages back as they were before the choice; saved so too when the choice saved them. */
  const undo = async () => {
    const step = said?.undo;
    if (!step || busy) return;
    const next = new Map(turns.shown);
    for (const index of step.changed) {
      const turn = step.before.get(index) ?? 0;
      if (turn) next.set(index, turn);
      else next.delete(index);
    }
    setSaid(null);
    apply(next);
    const failed = step.saved ? await saveFor(step.changed, next) : undefined;
    setSaid({ text: undoneWords(step.changed.length, failed), undo: null });
  };

  return { busy, checking, said, run, undo };
}

export type TurnWithAi = ReturnType<typeof useTurnWithAi>;

/** The check's status line in the turn menu (a polite live region), with Undo after a choice. */
export function TurnWithAiStatus({
  ai,
  className,
  buttonClassName,
}: {
  ai: TurnWithAi;
  className?: string;
  buttonClassName?: string;
}) {
  const text = ai.checking !== null ? `Checking ${pagesWord(ai.checking)}…` : ai.said?.text;
  return (
    <div className={className}>
      <span role="status">{text}</span>
      {ai.checking === null && ai.said?.undo ? (
        <button type="button" className={buttonClassName} disabled={ai.busy} onClick={() => void ai.undo()}>
          Undo
        </button>
      ) : null}
    </div>
  );
}
