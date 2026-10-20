"use client";

// One "Manage" menu per organisation row (owner, 16 Oct 2026; MonoEdge design system
// rules, misclick prevention), in place of the stacked buttons that ran past the table's
// right edge. The menu is drawn in a portal on <body>, with a 1px border and no shadow,
// placed by the button: below it, or above when there is no room below, and always inside
// the window (menuPlace). It opens below or above the button, so its first item is never
// under the pointer that opened it. Arrow keys, Home and End move between items; Esc
// closes it and gives focus back to the button; Tab closes it. The outside press that
// closes it is swallowed, so it never also acts on what is under it. The safe items come
// first; the ones that change the organisation's status come last, after a separator.
// Choosing an item puts focus back on the button first, so the dialog it opens returns
// focus there when it closes.

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import styles from "./Operator.module.css";

export interface ManageItem {
  label: string;
  onSelect(): void;
  danger?: boolean;
}

const GAP = 4; // px between the button and the menu
const MARGIN = 8; // px the menu keeps from the window's edges

type Box = { top: number; bottom: number; left: number; right: number };

/** Where the menu goes (fixed, in px): under the button, right edges lined up, or above
 * it when it would run past the bottom; then kept inside the window. */
export function menuPlace(
  button: Box,
  menu: { width: number; height: number },
  window: { width: number; height: number },
): { top: number; left: number } {
  const below = button.bottom + GAP;
  const above = button.top - GAP - menu.height;
  const fitsBelow = below + menu.height <= window.height - MARGIN;
  const top = fitsBelow || above < MARGIN ? below : above;
  const maxTop = Math.max(MARGIN, window.height - MARGIN - menu.height);
  const maxLeft = Math.max(MARGIN, window.width - MARGIN - menu.width);
  return {
    top: Math.min(Math.max(top, MARGIN), maxTop),
    left: Math.min(Math.max(button.right - menu.width, MARGIN), maxLeft),
  };
}

export function ManageMenu({
  name,
  safe,
  changes,
  buttonId,
}: {
  name: string;
  safe: ManageItem[];
  changes: ManageItem[];
  buttonId?: string;
}) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const menuId = useId();

  function close(refocus: boolean) {
    setOpen(false);
    if (refocus) button.current?.focus();
  }

  // Placed before it is painted, then the first item takes focus.
  useLayoutEffect(() => {
    const opener = button.current;
    const list = menu.current;
    if (!open || !opener || !list) return;
    const place = menuPlace(
      opener.getBoundingClientRect(),
      { width: list.offsetWidth, height: list.offsetHeight },
      { width: document.documentElement.clientWidth, height: window.innerHeight },
    );
    list.style.top = `${place.top}px`;
    list.style.left = `${place.left}px`;
    list.style.visibility = "visible";
    list.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
  }, [open]);

  // An outside press closes it and its click is swallowed; scrolling or resizing closes it
  // (it would no longer sit by its button).
  useEffect(() => {
    if (!open) return;
    const swallow = (event: MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();
    };
    const onPress = (event: PointerEvent) => {
      const target = event.target as Node;
      if (menu.current?.contains(target) || button.current?.contains(target)) return;
      setOpen(false);
      document.addEventListener("click", swallow, { capture: true, once: true });
      // A press with no click after it (a drag) must not swallow a later click.
      const forget = () => document.removeEventListener("click", swallow, { capture: true });
      document.addEventListener("pointerdown", forget, { capture: true, once: true });
    };
    const onMove = () => setOpen(false);
    document.addEventListener("pointerdown", onPress, true);
    window.addEventListener("resize", onMove, { passive: true });
    window.addEventListener("scroll", onMove, { capture: true, passive: true });
    return () => {
      document.removeEventListener("pointerdown", onPress, true);
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
  }, [open]);

  function keys(event: KeyboardEvent<HTMLDivElement>) {
    const items = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const at = items.indexOf(document.activeElement as HTMLElement);
    const go = (index: number) => {
      event.preventDefault();
      items[(index + items.length) % items.length]?.focus();
    };
    if (event.key === "ArrowDown") go(at + 1);
    else if (event.key === "ArrowUp") go(at - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(items.length - 1);
    else if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    } else if (event.key === "Tab") close(false);
  }

  function choose(item: ManageItem) {
    close(true);
    item.onSelect();
  }

  const entry = (item: ManageItem) => (
    <button
      key={item.label}
      type="button"
      role="menuitem"
      tabIndex={-1}
      className={item.danger ? `${styles.menuItem} ${styles.menuDanger}` : styles.menuItem}
      onClick={() => choose(item)}
    >
      {item.label}
    </button>
  );

  return (
    <>
      <button
        ref={button}
        id={buttonId}
        type="button"
        className="btn"
        aria-label={`Manage ${name}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close(false) : setOpen(true))}
        onKeyDown={(event) => {
          if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        Manage
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            id={menuId}
            role="menu"
            aria-label={`Manage ${name}`}
            className={styles.menu}
            onKeyDown={keys}
          >
            {safe.map(entry)}
            {safe.length > 0 && changes.length > 0 && <div role="separator" className={styles.menuSeparator} />}
            {changes.map(entry)}
          </div>,
          document.body,
        )}
    </>
  );
}
