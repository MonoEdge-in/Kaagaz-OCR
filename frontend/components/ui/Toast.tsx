"use client";

// A short note after an action (saved, uploaded, moved, deleted), with Undo
// where the action can be undone. It sits in a fixed corner (.toast in
// app/globals.css), so it never pushes the page under the pointer. The polite
// live region is always in the page, so the words are read out when they
// arrive. It goes after 8 seconds, but never while the pointer or the focus is
// on it, so there is time to reach Undo.

import { useEffect, useEffectEvent, useState } from "react";
import { IconButton } from "./IconButton";

export const TOAST_MS = 8000;

export interface ToastProps {
  /** null shows nothing. */
  message: string | null;
  onUndo?(): void;
  onClose(): void;
}

export function Toast({ message, onUndo, onClose }: ToastProps) {
  const [held, setHeld] = useState(false);
  const close = useEffectEvent(onClose);

  useEffect(() => {
    if (message === null || held) return;
    const timer = setTimeout(() => close(), TOAST_MS);
    return () => clearTimeout(timer);
  }, [message, held]);

  return (
    <div role="status" aria-live="polite">
      {message !== null && (
        <div
          className="toast"
          onPointerEnter={() => setHeld(true)}
          onPointerLeave={() => setHeld(false)}
          onFocus={() => setHeld(true)}
          onBlur={() => setHeld(false)}
        >
          <p>{message}</p>
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
