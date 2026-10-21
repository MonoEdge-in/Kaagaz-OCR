"use client";

// A dialog (Components.html, 12): the question as its title, what follows from
// it, any field it needs, then Cancel and the action. Dialog is the real thing,
// a native <dialog> opened with showModal(), so the browser gives the top
// layer, a focus trap, Esc to close and an inert page behind it; it closes on
// a press on the scrim too (only when the press and the release were both on
// it, so a drag out of the card never closes it). DialogPanel draws the same
// card in the page, for the components sheet.

import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Button, ButtonGroup } from "../Button/Button";
import styles from "./Dialog.module.css";

export interface DialogContentProps {
  /** Unique on the page; the title's id is `${id}-title`. */
  id: string;
  title: ReactNode;
  children?: ReactNode;
  /** Cancel and the action, in that order: the action last, on the right. */
  actions?: ReactNode;
  onClose(): void;
}

function Content({ id, title, children, actions, onClose }: DialogContentProps) {
  return (
    <>
      <div className={styles.head}>
        <h3 className="k-h3" id={`${id}-title`}>
          {title}
        </h3>
        <Button variant="quiet" size="sm" icon="x" label="Close" onClick={onClose} />
      </div>
      {children}
      {actions ? <ButtonGroup className={styles.actions}>{actions}</ButtonGroup> : null}
    </>
  );
}

export function DialogPanel(props: DialogContentProps) {
  return (
    <div className={styles.dialog} role="dialog" aria-labelledby={`${props.id}-title`}>
      <Content {...props} />
    </div>
  );
}

export function Dialog({ open, ...props }: DialogContentProps & { open: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  const pressedOnScrim = useRef(false);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={styles.native}
      aria-labelledby={`${props.id}-title`}
      onCancel={(event) => {
        event.preventDefault();
        props.onClose();
      }}
      onPointerDown={(event) => {
        pressedOnScrim.current = event.target === event.currentTarget;
      }}
      onClick={(event) => {
        if (pressedOnScrim.current && event.target === event.currentTarget) props.onClose();
        pressedOnScrim.current = false;
      }}
    >
      {open ? (
        <div className={styles.dialog}>
          <Content {...props} />
        </div>
      ) : null}
    </dialog>
  );
}
