"use client";

// A question before an action that cannot be taken back, such as Sign out
// everywhere. A native modal <dialog> (showModal), so the focus trap, Esc and
// an inert page behind it come from the browser. Cancel takes the focus when it
// opens, never the destructive button (MonoEdge rules, misclick prevention).
// The backdrop closes it only when the press and the release were both on it,
// so a drag that ends outside never does.

import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { isBackdropClick } from "./drawerLogic";
import styles from "./ConfirmDialog.module.css";

export interface ConfirmDialogProps {
  open: boolean;
  /** Names the thing: "Sign out everywhere?" */
  title: string;
  /** The destructive button's words. */
  confirmLabel: string;
  busy?: boolean;
  error?: string | null;
  onConfirm(): void;
  onCancel(): void;
  children: ReactNode;
}

export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  busy = false,
  error = null,
  onConfirm,
  onCancel,
  children,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const pressTarget = useRef<EventTarget | null>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !open) return;
    if (!dialog.open) dialog.showModal();
    cancelRef.current?.focus(); // showModal would focus the first button, the destructive one
    return () => dialog.close();
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      className={styles.dialog}
      aria-labelledby={titleId}
      onCancel={(event) => {
        // Esc: let the parent's state close it, so the two never disagree.
        event.preventDefault();
        if (open) onCancel();
      }}
      onClose={() => {
        if (open) onCancel(); // the browser closed it itself
      }}
      onPointerDown={(event) => {
        pressTarget.current = event.target;
      }}
      onClick={(event) => {
        if (isBackdropClick(pressTarget.current, event.target, event.currentTarget)) onCancel();
        pressTarget.current = null;
      }}
    >
      <div className={styles.panel}>
        <h2 id={titleId} className={styles.title}>
          {title}
        </h2>
        {children}
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className="actions">
          <button
            type="button"
            className="btn btn-destructive"
            onClick={onConfirm}
            disabled={busy}
            aria-busy={busy || undefined}
          >
            {confirmLabel}
          </button>
          <button ref={cancelRef} type="button" className="btn" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </dialog>
  );
}
