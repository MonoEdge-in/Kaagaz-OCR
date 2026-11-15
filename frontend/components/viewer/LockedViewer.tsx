"use client";

// A locked file's pages (PRD 7 section 6, PR 13): only its page pictures, one at a time, each an
// image from its own route (lockedPageUrl), never the original file, pdf.js or the image
// optimizer (unoptimized). Each page keeps its turn for everyone (#600). No download, print,
// text or panels: the pictures are not draggable, and the viewer is hidden in print
// (Locked.module.css). A countdown shows how long the 10-minute check has left; when it ends,
// or a picture is refused because the check ended (sign-out elsewhere, a new password), it
// hands back to the prompt (onEnded).

import Image from "next/image";
import { useEffect, useState } from "react";
import { getLockedOpen, lockedPageUrl } from "@/lib/api";
import { useTick } from "@/lib/tick";
import type { PageSummary, PageTurnSaved } from "@/lib/types";
import { LOCKED_TEXT, openForText, pageOfText, secondsLeft, turnStyle } from "./locked";
import styles from "./Locked.module.css";

export interface LockedViewerProps {
  documentId: string;
  name: string;
  pages: readonly PageSummary[];
  turns: readonly PageTurnSaved[];
  until: string; // ISO 8601: the end of the 10-minute check
  onEnded(): void;
}

export function LockedViewer({ documentId, name, pages, turns, until, onEnded }: LockedViewerProps) {
  const shown = pages.filter((page) => page.has_image);
  const [index, setIndex] = useState(0);
  const [failed, setFailed] = useState<number | null>(null); // the page whose picture did not come
  // The screen's one shared clock (lib/tick.ts) counts down, from the wall clock when it opened.
  const [opened] = useState(() => ({ at: Date.now(), tick: performance.now() }));
  const tick = useTick();
  const left = secondsLeft(until, opened.at + Math.max(0, tick - opened.tick));

  useEffect(() => {
    if (left === 0) onEnded();
  }, [left, onEnded]);

  const page = shown[Math.min(index, shown.length - 1)];
  if (!page) return <p className="muted">{LOCKED_TEXT.preparing}</p>;
  const turn = turns.find((each) => each.page_no === page.page_no)?.turn ?? 0;

  // A picture refused: back to the prompt when the check has ended, else say the page failed.
  const pictureFailed = () => {
    setFailed(page.page_no);
    getLockedOpen().then(
      (state) => {
        if (!state.open) onEnded();
      },
      () => undefined,
    );
  };

  return (
    <section className={styles.viewer} aria-label={`${name}, view only`}>
      <div className={styles.toolbar}>
        <div className={styles.pages}>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => setIndex((at) => at - 1)}
            disabled={index === 0}
          >
            {LOCKED_TEXT.previous}
          </button>
          <span aria-live="polite">{pageOfText(index, shown.length)}</span>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => setIndex((at) => at + 1)}
            disabled={index >= shown.length - 1}
          >
            {LOCKED_TEXT.next}
          </button>
        </div>
        <span>{openForText(left)}</span>
      </div>
      <p className="muted">{LOCKED_TEXT.viewOnly}</p>
      <div className={styles.frame}>
        {failed === page.page_no ? (
          <p className="error-text" role="alert">
            {LOCKED_TEXT.pageFailed}
          </p>
        ) : (
          <Image
            key={page.page_no}
            className={styles.picture}
            src={lockedPageUrl(documentId, page.page_no)}
            alt={`${name}, ${pageOfText(index, shown.length).toLowerCase()}`}
            width={Math.round(page.width)}
            height={Math.round(page.height)}
            unoptimized
            draggable={false}
            style={turnStyle(turn)}
            onError={pictureFailed}
          />
        )}
      </div>
    </section>
  );
}
