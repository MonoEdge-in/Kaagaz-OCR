"use client";

// A short note after an action (saved, uploaded, moved, deleted), with Undo
// where the action can be undone. It sits in a fixed corner (.toast in
// app/globals.css), so it never pushes the page under the pointer. The polite
// live region is always in the page, so the words are read out when they
// arrive. It goes after 8 seconds, but never while the pointer or the focus is
// on it, so there is time to reach Undo. Closed, it fades out (120ms, .toast[data-leaving]
// in app/globals.css) before it leaves the page; it takes no clicks while it goes.

import { useEffect, useEffectEvent, useState } from "react";
import { IconButton } from "./IconButton";

export const TOAST_MS = 8000;
const LEAVE_MS = 120; // --t-fast

export interface ToastProps {
  /** null shows nothing. */
  message: string | null;
  onUndo?(): void;
  onClose(): void;
}

export function Toast({ message, onUndo, onClose }: ToastProps) {
  const [held, setHeld] = useState(false);
  const close = useEffectEvent(onClose);
  // The words last shown, kept while the toast fades out after `message` goes null.
  const [shown, setShown] = useState(message);
  if (message !== null && message !== shown) setShown(message);
  const leaving = message === null && shown !== null;

  useEffect(() => {
    if (!leaving) return;
    const timer = setTimeout(() => setShown(null), LEAVE_MS);
    return () => clearTimeout(timer);
  }, [leaving]);

  useEffect(() => {
    if (message === null || held) return;
    const timer = setTimeout(() => close(), TOAST_MS);
    return () => clearTimeout(timer);
  }, [message, held]);

  return (
    <div role="status" aria-live="polite">
      {shown !== null && (
        <div
          className="toast"
          data-leaving={leaving || undefined}
          onPointerEnter={() => setHeld(true)}
          onPointerLeave={() => setHeld(false)}
          onFocus={() => setHeld(true)}
          onBlur={() => setHeld(false)}
        >
          <p>{shown}</p>
          {onUndo && (
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => {
                onUndo();
                onClose();
              }}
            >
              Undo
            </button>
          )}
          <IconButton icon="close" label="Dismiss" className="btn-quiet" onClick={onClose} />
        </div>
      )}
    </div>
  );
}
