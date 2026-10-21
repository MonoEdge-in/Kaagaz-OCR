"use client";

// A side panel (Components.html, 12): a head bar with the title and Close, the
// body, and a footer with Cancel and the action ("Move here"). Drawer is a
// native <dialog> opened with showModal() on the right edge, closing on Esc and
// on a press on the scrim, as Dialog does. DrawerPanel draws the same panel in
// the page, for the components sheet.

import { useEffect, useRef } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Button } from "../Button/Button";
import styles from "./Drawer.module.css";

export interface DrawerContentProps {
  /** Unique on the page; the title's id is `${id}-title`. */
  id: string;
  title: ReactNode;
  children: ReactNode;
  /** Cancel and the action, the action last. */
  footer?: ReactNode;
  onClose(): void;
}

function Content({ id, title, children, footer, onClose }: DrawerContentProps) {
  return (
    <>
      <div className={styles.head}>
        <h3 className="k-h4" id={`${id}-title`}>
          {title}
        </h3>
        <Button variant="quiet" size="sm" icon="x" label="Close" onClick={onClose} />
      </div>
      <div className={styles.body}>{children}</div>
      {footer ? <div className={styles.foot}>{footer}</div> : null}
    </>
  );
}

export function DrawerPanel({ width, ...props }: DrawerContentProps & { width?: number }) {
  return (
    <aside
      className={styles.drawer}
      aria-labelledby={`${props.id}-title`}
      style={width ? ({ width } as CSSProperties) : undefined}
    >
      <Content {...props} />
    </aside>
  );
}

export function Drawer({ open, ...props }: DrawerContentProps & { open: boolean }) {
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
        <div className={styles.drawer}>
          <Content {...props} />
        </div>
      ) : null}
    </dialog>
  );
}
