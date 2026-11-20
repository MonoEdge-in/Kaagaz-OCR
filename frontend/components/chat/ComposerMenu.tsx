"use client";

// A quiet button in the composer's toolbar that opens a small panel just above it:
// the mode, the scope, the answer settings. A native popover, so Esc or a click
// outside closes it and focus goes back to the button. Opening moves focus to the
// chosen option inside (or the panel itself when its choices are locked). Enter on
// a choice closes the panel; it never sends the question. CSS anchor positioning
// places it (Chat.module.css, .menu).

import { useId } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import styles from "./Chat.module.css";

/** Close a panel opened by ComposerMenu; focus goes back to its button. */
export function closeMenu(inside: Element) {
  inside.closest<HTMLElement>("[popover]")?.hidePopover();
}

/** Enter on a choice in a panel closes it, instead of sending the question (the panel sits in the
 * question's form); in a search box (#725: the scope's find box) it only never sends: the panel
 * stays open for the typing. */
function closeOnEnter(event: KeyboardEvent<HTMLElement>) {
  if (event.key === "Enter" && event.target instanceof HTMLInputElement) {
    event.preventDefault();
    if (event.target.type !== "search") event.currentTarget.hidePopover();
  }
}

export function ComposerMenu({
  label,
  name,
  title,
  end = false,
  onToggle,
  children,
}: {
  label: ReactNode; // what the button shows: the current choice
  name: string; // what the button is for, for screen readers ("Answer settings")
  title: string; // the panel's name for screen readers
  end?: boolean; // line the panel up with the button's end (the toolbar's right side)
  onToggle?: (open: boolean) => void;
  children: ReactNode;
}) {
  const id = useId();
  const anchor = `--menu-${id.replace(/[^\w-]/g, "")}`;
  return (
    <>
      <button
        type="button"
        className={styles.tool}
        popoverTarget={id}
        aria-haspopup="dialog"
        style={{ anchorName: anchor }}
      >
        <span className="visually-hidden">{name}: </span>
        <span className={styles.toolText}>{label}</span>
        <Icon name="chevron-right" size={14} className={styles.caret} />
      </button>
      <div
        id={id}
        popover="auto"
        role="dialog"
        aria-label={title}
        tabIndex={-1}
        className={`motion-pop ${styles.menu} ${end ? styles.menuEnd : ""}`}
        style={{ positionAnchor: anchor }}
        onKeyDown={closeOnEnter}
        onToggle={(event) => {
          const open = event.newState === "open";
          if (open) {
            const menu = event.currentTarget;
            const first =
              menu.querySelector<HTMLElement>("input:checked:enabled") ??
              menu.querySelector<HTMLElement>("input:enabled, select:enabled, button:enabled");
            (first ?? menu).focus();
          }
          onToggle?.(open);
        }}
      >
        {children}
      </div>
    </>
  );
}
