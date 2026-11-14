"use client";

// The turn menu for photos and scanned images (#600, the lead's decision 4: sideways phone photos
// are common), the same choices as the PDF viewer's (#589): this page or all pages a quarter turn
// left or right, every page made wide or tall, and Reset to original. It stays open, so an upside
// down page takes two presses. A choice shows on this screen only, until "Save for everyone"
// beside it (SavePageTurns.tsx). A native popover: Esc or a press outside closes it.

import { useId } from "react";
import type { ReactNode } from "react";
import { turnPages, turnPagesTo } from "@/components/extend/lib/page-turns";
import type { PageTurns } from "@/components/extend/lib/page-turns";
import { IconButton } from "@/components/ui/IconButton";
import type { PageSummary, PageTurnSaved } from "@/lib/types";
import { SaveForEveryone, usePageTurns } from "./SavePageTurns";
import styles from "./PageViewer.module.css";

type Turns = ReturnType<typeof usePageTurns>;

/** Holds a file's page turns for the page viewer drawn inside it (keyed by the file). */
export function WithPageTurns({
  documentId,
  pageTurns,
  userId,
  children,
}: {
  documentId: string;
  pageTurns: readonly PageTurnSaved[];
  userId: string;
  children: (turns: Turns) => ReactNode;
}) {
  return children(usePageTurns(documentId, pageTurns, userId));
}

export function PageTurnMenu({
  turns,
  pages,
  pageNo,
  documentId,
  userId,
}: {
  turns: Turns;
  /** The file's pages, by number from 1. */
  pages: readonly PageSummary[];
  pageNo: number;
  documentId: string;
  userId: string;
}) {
  const id = useId();
  const anchor = `--turn-menu-${id.replace(/[^\w-]/g, "")}`;
  const shown = turns.shown as PageTurns; // quarter turns 1 to 3
  // A photo's size is as it shows, with no turn of its own in the file.
  const shapes = pages.map((page) => ({ size: { width: page.width, height: page.height }, rotation: 0 }));
  const rows = [
    { label: "This page", indexes: [pageNo - 1] },
    { label: "All pages", indexes: pages.map((page) => page.page_no - 1) },
  ];
  return (
    <>
      <IconButton
        icon="refresh"
        label="Turn pages"
        className="btn-quiet"
        popoverTarget={id}
        aria-haspopup="true"
        style={{ anchorName: anchor }}
      />
      <div
        id={id}
        popover="auto"
        aria-label="Turn pages"
        className={`motion-pop ${styles.turnMenu}`}
        style={{ positionAnchor: anchor }}
      >
        {rows.map((row) => (
          <div key={row.label} role="group" aria-label={row.label} className={styles.turnRow}>
            <span>{row.label}</span>
            <button type="button" className="btn btn-quiet" onClick={() => turns.show(turnPages(shown, row.indexes, -1))}>
              Turn left
            </button>
            <button type="button" className="btn btn-quiet" onClick={() => turns.show(turnPages(shown, row.indexes, 1))}>
              Turn right
            </button>
          </div>
        ))}
        <button type="button" className="btn btn-quiet" onClick={() => turns.show(turnPagesTo(shown, shapes, "wide"))}>
          Make all pages wide
        </button>
        <button type="button" className="btn btn-quiet" onClick={() => turns.show(turnPagesTo(shown, shapes, "tall"))}>
          Make all pages tall
        </button>
        <button type="button" className="btn btn-quiet" onClick={() => turns.show(new Map())}>
          Reset to original
        </button>
      </div>
      <SaveForEveryone
        documentId={documentId}
        userId={userId}
        saved={turns.saved}
        shown={turns.shown}
        onSaved={turns.takeSaved}
        className="btn btn-quiet"
      />
    </>
  );
}
