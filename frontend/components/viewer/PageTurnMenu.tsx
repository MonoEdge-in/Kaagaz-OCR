"use client";

// The turn menu for photos and scanned images (#600, the lead's decision 4: sideways phone photos
// are common), the same choices as the PDF viewer's (#589): this page or all pages a quarter turn
// left or right, every page made wide or tall, and Reset to original; a file of one page has only
// its own row and Reset. #709: wide and tall ask the orientation check, and "Straighten all pages
// with AI" (a file of one page too) turns them upright and saves them (useTurnWithAi). It stays
// open, so an upside down page takes two presses, and waits while a save or the check is on its
// way. A choice shows on this screen only, until "Save for everyone" beside it (SavePageTurns.tsx). A native popover, a dialog as the chat's composer menus are: Esc or a press
// outside closes it.

import { useId } from "react";
import type { ReactNode } from "react";
import { turnPages } from "@/components/extend/lib/page-turns";
import type { PageTurns } from "@/components/extend/lib/page-turns";
import { IconButton } from "@/components/ui/IconButton";
import type { PageSummary, PageTurnSaved } from "@/lib/types";
import { SaveForEveryone, TurnWithAiStatus, usePageTurns, useTurnWithAi } from "./SavePageTurns";
import type { AiTurnChoice, PageTurnsState } from "./SavePageTurns";
import styles from "./PageViewer.module.css";

/** Holds a file's page turns for the page viewer drawn inside it (keyed by the file). */
export function WithPageTurns({
  documentId,
  pageTurns,
  userId,
  children,
}: {
  documentId: string;
  pageTurns: readonly PageTurnSaved[] | undefined;
  userId: string;
  children: (turns: PageTurnsState) => ReactNode;
}) {
  return children(usePageTurns(documentId, pageTurns, userId));
}

export function PageTurnMenu({
  turns,
  pages,
  pageNo,
  documentId,
  userId,
  onTurnsSaved,
}: {
  turns: PageTurnsState;
  /** The file's pages, by number from 1. */
  pages: readonly PageSummary[];
  pageNo: number;
  documentId: string;
  userId: string;
  /** After the turns are saved for everyone (the screen reloads the file's detail). */
  onTurnsSaved?: () => void;
}) {
  const id = useId();
  const anchor = `--turn-menu-${id.replace(/[^\w-]/g, "")}`;
  const shown = turns.shown as PageTurns; // quarter turns 1 to 3
  const many = pages.length > 1;
  // A photo's size is as it shows, with no turn of its own in the file.
  const shapes = pages.map((page) => ({ size: { width: page.width, height: page.height }, rotation: 0 }));
  const rows = [
    { label: "This page", indexes: [pageNo - 1] },
    ...(many ? [{ label: "All pages", indexes: pages.map((page) => page.page_no - 1) }] : []),
  ];
  // #709: wide, tall and Straighten ask the orientation check (useTurnWithAi); every choice waits for it.
  const ai = useTurnWithAi(documentId, userId, turns, turns.show, onTurnsSaved);
  const choice = (label: string, next: () => Map<number, number>) => (
    <button type="button" className="btn btn-quiet" disabled={ai.busy} onClick={() => turns.show(next())}>
      {label}
    </button>
  );
  const aiChoice = (label: string, picked: AiTurnChoice) => (
    <button type="button" className="btn btn-quiet" disabled={ai.busy} onClick={() => void ai.run(picked, shapes)}>
      {label}
    </button>
  );
  return (
    <>
      <IconButton
        icon="refresh"
        label="Turn pages"
        className="btn-quiet"
        popoverTarget={id}
        aria-haspopup="dialog"
        style={{ anchorName: anchor }}
      />
      <div
        id={id}
        popover="auto"
        role="dialog"
        aria-label="Turn pages"
        className={`motion-pop ${styles.turnMenu}`}
        style={{ positionAnchor: anchor }}
      >
        {rows.map((row) => (
          <div key={row.label} role="group" aria-label={row.label} className={styles.turnRow}>
            <span>{row.label}</span>
            {choice("Turn left", () => turnPages(shown, row.indexes, -1))}
            {choice("Turn right", () => turnPages(shown, row.indexes, 1))}
          </div>
        ))}
        {many ? aiChoice("Make all pages wide", "wide") : null}
        {many ? aiChoice("Make all pages tall", "tall") : null}
        {aiChoice("Straighten all pages with AI", "straighten")}
        {choice("Reset to original", () => new Map())}
        <TurnWithAiStatus ai={ai} className={styles.turnStatus} buttonClassName="btn btn-quiet" />
      </div>
      <SaveForEveryone
        documentId={documentId}
        userId={userId}
        turns={turns}
        onSaved={onTurnsSaved}
        className="btn btn-quiet"
      />
    </>
  );
}
