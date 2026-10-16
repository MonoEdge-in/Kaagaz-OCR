"use client";

// A side panel for small screens (the app menu, the chat's conversations).
// It is a native <dialog> opened with showModal(), so the browser gives us the
// top layer, a focus trap, Esc to close and an inert page behind it. On top of
// that it closes on a backdrop click (only when the press and the release were
// both on the backdrop, so a drag out of the panel never closes it), on a route
// change and once the screen is wide enough for the sidebar, and hands focus
// back to whatever opened it. With unsaved changes, Esc, the backdrop and the
// close button ask first. Its content renders only while it is open, so
// nothing inside a closed drawer can be reached with Tab.

import { usePathname } from "next/navigation";
import { useEffect, useEffectEvent, useRef } from "react";
import type { ReactNode } from "react";
import {
  closeAllowed,
  closesOnRoute,
  closesOnViewport,
  DESKTOP_QUERY,
  isBackdropClick,
  shouldReturnFocus,
} from "./drawerLogic";
import { IconButton } from "./IconButton";
import styles from "./Drawer.module.css";

export interface DrawerProps {
  open: boolean;
  onClose(): void;
  title: string;
  id: string;
  /** Changes inside that a close would lose: Esc, the backdrop and Close ask first. */
  unsaved?: boolean;
  children: ReactNode;
}

const UNSAVED_QUESTION = "Close without saving? Your changes here will be lost.";

export function Drawer({ open, onClose, title, id, unsaved = false, children }: DrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const pressTarget = useRef<EventTarget | null>(null);
  const lastPath = useRef<string | null>(null);
  const pathname = usePathname();
  const titleId = `${id}-title`;
  const askClose = () => {
    if (closeAllowed(unsaved, () => window.confirm(UNSAVED_QUESTION))) onClose();
  };

  const closeForRoute = useEffectEvent((previous: string, next: string) => {
    if (closesOnRoute(previous, next, open)) onClose();
  });
  const closeForViewport = useEffectEvent((desktop: boolean) => {
    if (closesOnViewport(desktop, open)) onClose();
  });

  // Open and close the native dialog to match `open`.
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog.open) dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
      if (opener?.isConnected && shouldReturnFocus(document.activeElement, document.body, dialog)) {
        opener.focus();
      }
    };
  }, [open]);

  // Following a link inside the drawer changes the route: close it.
  useEffect(() => {
    const previous = lastPath.current;
    lastPath.current = pathname;
    if (previous !== null) closeForRoute(previous, pathname);
  }, [pathname]);

  // Once the sidebar shows (1024 px and up), the drawer is not needed.
  useEffect(() => {
    if (!open) return;
    const query = window.matchMedia(DESKTOP_QUERY);
    closeForViewport(query.matches);
    const change = (event: MediaQueryListEvent) => closeForViewport(event.matches);
    query.addEventListener("change", change);
    return () => query.removeEventListener("change", change);
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      id={id}
      className={`${styles.dialog} ${styles.start}`}
      aria-labelledby={titleId}
      onCancel={(event) => {
        // Esc: let the parent's state close it, so the two never disagree.
        event.preventDefault();
        if (open) askClose();
      }}
      onClose={() => {
        // The browser closed it itself (some Esc presses cannot be cancelled).
        if (open) onClose();
      }}
      onPointerDown={(event) => {
        pressTarget.current = event.target;
      }}
      onClick={(event) => {
        if (isBackdropClick(pressTarget.current, event.target, event.currentTarget)) askClose();
        pressTarget.current = null;
      }}
    >
      {open && (
        <div className={styles.panel}>
          <div className={styles.header}>
            <h2 id={titleId} className={styles.title}>
              {title}
            </h2>
            <IconButton icon="close" label={`Close ${title.toLowerCase()}`} className="btn-quiet" onClick={askClose} />
          </div>
          <div className={styles.body}>{children}</div>
        </div>
      )}
    </dialog>
  );
}
