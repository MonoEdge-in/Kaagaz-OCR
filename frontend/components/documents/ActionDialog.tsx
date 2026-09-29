"use client";

// A small centred dialog for one change to one document (rename, move, tags).
// It is a native <dialog> opened with showModal(), so the browser gives the
// top layer, a focus trap, Esc to close and an inert page behind it. It shows
// only while mounted: the caller renders it to open it and removes it to close
// it. It focuses the field marked data-autofocus, closes on a backdrop click
// (the same rule as the Drawer), and calls onClosed once it is shut, when the
// page can take focus again.

import { useEffect, useEffectEvent, useId, useRef } from "react";
import type { ReactNode } from "react";
import { isBackdropClick } from "@/components/ui/drawerLogic";
import { IconButton } from "@/components/ui/IconButton";
import styles from "./ActionDialog.module.css";

export interface ActionDialogProps {
  title: string;
  onClose(): void;
  onClosed?(): void;
  children: ReactNode;
}

export function ActionDialog({ title, onClose, onClosed, children }: ActionDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const pressTarget = useRef<EventTarget | null>(null);
  const titleId = useId();
  const afterClose = useEffectEvent(() => onClosed?.());

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    dialog.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    return () => {
      if (dialog.open) dialog.close();
      afterClose();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className={styles.dialog}
      aria-labelledby={titleId}
      onCancel={(event) => {
        // Esc: let the caller's state close it, so the two never disagree.
        event.preventDefault();
        onClose();
      }}
      onPointerDown={(event) => {
        pressTarget.current = event.target;
      }}
      onClick={(event) => {
        if (isBackdropClick(pressTarget.current, event.target, event.currentTarget)) onClose();
        pressTarget.current = null;
      }}
    >
      <div className={styles.panel}>
        <div className={styles.header}>
          <h2 id={titleId} className={styles.title}>
            {title}
          </h2>
          <IconButton icon="close" label="Close" className="btn-quiet" onClick={onClose} />
        </div>
        <div className={styles.body}>{children}</div>
      </div>
    </dialog>
  );
}
