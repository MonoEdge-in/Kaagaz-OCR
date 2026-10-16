"use client";

// One row's actions in a small menu (UI audit AD2 and A1, 16 Oct 2026): a quiet
// "More" button opens a native popover, so Esc or a click outside closes it and
// focus goes back to the button. Up and Down move between the actions, Home and
// End go to the ends. A destructive action sits last, under a hairline. CSS anchor
// positioning puts the menu under its button, flipped above near the window's foot
// (Admin.module.css, .rowMenu); without it the browser centres the menu.

import { useId } from "react";
import type { KeyboardEvent } from "react";
import { Icon } from "@/components/ui/Icon";
import styles from "./Admin.module.css";

export interface RowMenuItem {
  label: string;
  onSelect: () => void;
  destructive?: boolean;
  /** Shown under the action, which then does nothing: why it can't be used yet. */
  disabledReason?: string | null;
}

/** Up, Down, Home and End: the next action to focus, from `at` of `count`. */
export function menuKeyTarget(key: string, at: number, count: number): number | null {
  if (count === 0) return null;
  if (key === "ArrowDown") return (at + 1) % count;
  if (key === "ArrowUp") return (at - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
  const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')];
  const next = menuKeyTarget(event.key, items.indexOf(event.target as HTMLButtonElement), items.length);
  if (next === null) return;
  event.preventDefault();
  items[next]?.focus();
}

/** `name` says whose actions they are ("Ravi Sharma"), for screen readers. */
export function RowMenu({ name, items }: { name: string; items: RowMenuItem[] }) {
  const id = useId();
  const anchor = `--row-menu-${id.replace(/[^\w-]/g, "")}`;
  if (items.length === 0) return null;
  return (
    <>
      <button
        type="button"
        className={`btn btn-quiet ${styles.rowMenuButton}`}
        popoverTarget={id}
        aria-haspopup="menu"
        style={{ anchorName: anchor }}
      >
        More
        <span className="visually-hidden"> actions for {name}</span>
        <Icon name="chevron-right" size={14} className={styles.rowMenuCaret} />
      </button>
      <div
        id={id}
        popover="auto"
        role="menu"
        aria-label={`Actions for ${name}`}
        className={`motion-pop ${styles.rowMenu}`}
        style={{ positionAnchor: anchor }}
        onKeyDown={moveFocus}
        onToggle={(event) => {
          if (event.newState === "open") event.currentTarget.querySelector<HTMLElement>('[role="menuitem"]')?.focus();
        }}
      >
        {items.map((item) => (
          <button
            key={item.label}
            type="button"
            role="menuitem"
            className={item.destructive ? `${styles.rowMenuItem} ${styles.rowMenuDanger}` : styles.rowMenuItem}
            aria-disabled={item.disabledReason ? true : undefined}
            onClick={(event) => {
              if (item.disabledReason) return;
              event.currentTarget.closest<HTMLElement>("[popover]")?.hidePopover();
              item.onSelect();
            }}
          >
            {item.label}
            {item.disabledReason && <span className={styles.rowMenuHint}>{item.disabledReason}</span>}
          </button>
        ))}
      </div>
    </>
  );
}
