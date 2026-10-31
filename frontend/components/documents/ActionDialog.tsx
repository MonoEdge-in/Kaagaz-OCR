"use client";

// A small centred dialog for one change to one document (rename, move, tags).
// It is a native <dialog> opened with showModal(), so the browser gives the
// top layer, a focus trap, Esc to close and an inert page behind it. It shows
// only while mounted: the caller renders it to open it and removes it to close
// it. It focuses the field marked data-autofocus (else its first field), closes
// on a backdrop click (the same rule as the Drawer), and calls onClosed once it
// is shut, when the page can take focus again; if onClosed places focus nowhere,
// it goes back to whatever opened the dialog. canClose, when given, can refuse a way of closing
// (the Close button, Esc, a backdrop click), e.g. while a request runs; without
// it every way closes, as before.

import { useEffect, useEffectEvent, useId, useRef } from "react";
import type { ReactNode } from "react";
import { isBackdropClick, shouldReturnFocus } from "@/components/ui/drawerLogic";
import { IconButton } from "@/components/ui/IconButton";
import styles from "./ActionDialog.module.css";

export type CloseWay = "button" | "escape" | "backdrop";

export interface ActionDialogProps {
  title: string;
  onClose(): void;
  onClosed?(): void;
  canClose?(way: CloseWay): boolean;
  /** Up to 56rem wide, for a form with a panel beside its fields (#463); else 30rem. */
  wide?: boolean;
  children: ReactNode;
}

export function ActionDialog({ title, onClose, onClosed, canClose = () => true, wide = false, children }: ActionDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const pressTarget = useRef<EventTarget | null>(null);
  const titleId = useId();
  const afterClose = useEffectEvent(() => onClosed?.());

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    (dialog.querySelector<HTMLElement>("[data-autofocus]") ?? dialog.querySelector<HTMLElement>("input, select, textarea"))?.focus();
    return () => {
      if (dialog.open) dialog.close();
      afterClose();
      if (opener?.isConnected && shouldReturnFocus(document.activeElement, document.body, dialog)) opener.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className={wide ? `${styles.dialog} ${styles.wide}` : styles.dialog}
      aria-modal="true"
      aria-labelledby={titleId}
      onCancel={(event) => {
        // Esc: let the caller's state close it, so the two never disagree.
        event.preventDefault();
        if (canClose("escape")) onClose();
      }}
      onPointerDown={(event) => {
        pressTarget.current = event.target;
      }}
      onClick={(event) => {
        if (isBackdropClick(pressTarget.current, event.target, event.currentTarget) && canClose("backdrop")) onClose();
        pressTarget.current = null;
      }}
    >
      <div className={styles.panel}>
        <div className={styles.header}>
          <h2 id={titleId} className={styles.title}>
            {title}
          </h2>
          <IconButton icon="close" label="Close" className="btn-quiet" onClick={onClose} disabled={!canClose("button")} />
        </div>
        <div className={styles.body}>{children}</div>
      </div>
    </dialog>
  );
}
