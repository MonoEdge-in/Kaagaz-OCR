"use client";

// A locked file's pages (PRD 7 section 6, PR 13): only its page pictures, one at a time, each an
// image from its own route (lockedPageUrl), never the original file, pdf.js or the image
// optimizer (unoptimized). Each page keeps its turn for everyone (#600), in a frame of its turned
// shape (turnedPicture). No download, print, text or panels: the pictures are not draggable, the
// frame has no context menu (Save image as), and the viewer and its pictures are hidden in print
// (Locked.module.css). These only deter: the browser's developer tools and a photograph of the
// screen remain. A countdown shows how long the 10-minute check has left, from the server's
// `seconds` and performance.now(), never this PC's clock; when it ends, or a picture is refused
// because the check ended (sign-out elsewhere, a new password), it hands back to the prompt
// (onEnded). A page that failed while the check is live says so, with Try again.

import Image from "next/image";
import { useEffect, useState } from "react";
import { getLockedOpen, lockedPageUrl } from "@/lib/api";
import { useTick } from "@/lib/tick";
import type { PageSummary, PageTurnSaved } from "@/lib/types";
import { LOCKED_TEXT, openForText, pageOfText, secondsLeft, turnedPicture } from "./locked";
import styles from "./Locked.module.css";

export interface LockedViewerProps {
  documentId: string;
  name: string;
  pages: readonly PageSummary[];
  turns: readonly PageTurnSaved[];
  seconds: number; // the check's seconds left, by the server's clock, when it was read
  onEnded(): void;
}

export function LockedViewer({ documentId, name, pages, turns, seconds, onEnded }: LockedViewerProps) {
  const shown = pages.filter((page) => page.has_image);
  const [index, setIndex] = useState(0);
  const [failed, setFailed] = useState(false); // the page shown did not come
  const [tries, setTries] = useState(0); // Try again draws the page's picture anew
  // The screen's one shared clock (lib/tick.ts, performance.now()) counts down from when it opened.
  const [readAt] = useState(() => performance.now());
  const tick = useTick();
  const left = secondsLeft(seconds, readAt, tick);

  useEffect(() => {
    if (left === 0) onEnded();
  }, [left, onEnded]);

  const page = shown[Math.min(index, shown.length - 1)];
  if (!page) return <p className="muted">{LOCKED_TEXT.preparing}</p>;
  const turn = turns.find((each) => each.page_no === page.page_no)?.turn ?? 0;
  const turned = turnedPicture(page, turn);

  const turnTo = (next: number) => {
    setFailed(false);
    setIndex(next);
  };
  // A picture refused: back to the prompt when the check has ended, else say the page failed.
  const pictureFailed = () => {
    setFailed(true);
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
          <button type="button" className="btn btn-quiet" onClick={() => turnTo(index - 1)} disabled={index === 0}>
            {LOCKED_TEXT.previous}
          </button>
          <span aria-live="polite">{pageOfText(index, shown.length)}</span>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => turnTo(index + 1)}
            disabled={index >= shown.length - 1}
          >
            {LOCKED_TEXT.next}
          </button>
        </div>
        <span>{openForText(left)}</span>
      </div>
      <p className="muted">{LOCKED_TEXT.viewOnly}</p>
      <div className={styles.frame} onContextMenu={(event) => event.preventDefault()}>
        {failed ? (
          <div role="alert">
            <p className="error-text">{LOCKED_TEXT.pageFailed}</p>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setFailed(false);
                setTries((n) => n + 1);
              }}
            >
              {LOCKED_TEXT.tryAgain}
            </button>
          </div>
        ) : (
          <div className={styles.page} style={turned.frame}>
            <Image
              key={`${page.page_no}:${tries}`}
              className={styles.picture}
              src={lockedPageUrl(documentId, page.page_no)}
              alt={`${name}, ${pageOfText(index, shown.length).toLowerCase()}`}
              width={Math.round(page.width)}
              height={Math.round(page.height)}
              unoptimized
              draggable={false}
              style={turned.picture}
              onError={pictureFailed}
            />
          </div>
        )}
      </div>
    </section>
  );
}
