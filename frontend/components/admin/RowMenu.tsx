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
import { IconButton } from "@/components/ui/IconButton";
import styles from "./Admin.module.css";

export interface RowMenuItem {
  label: string;
  onSelect: () => void;
  destructive?: boolean;
  /** One plain line under the label: what happens, and to whom (plain words, 25 Oct 2026). */
  description?: string;
  /** Shown under the action in place of its description, and the action then does nothing:
   * why it can't be used yet. */
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

/** The key's action takes focus and is kept in view, the menu scrolling only as far as it needs (#711). */
export function moveFocus(event: KeyboardEvent<HTMLDivElement>) {
  const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')];
  const next = menuKeyTarget(event.key, items.indexOf(event.target as HTMLButtonElement), items.length);
  if (next === null) return;
  event.preventDefault();
  items[next]?.focus({ preventScroll: true });
  items[next]?.scrollIntoView({ block: "nearest" });
}

/**
 * `name` says whose actions they are ("Ravi Sharma"), for screen readers. `compact`: an
 * icon-only ⋯ button instead of "More" (a narrow list, Ask screen polish, 17 Oct 2026).
 * `status`: the compact button's own name, tooltip and class when it also carries a state
 * (a summary card value not checked yet, #453). `label`: the button's words in place of "More"
 * (PRD 9 F-17: Review's "Filter: Any reason"), or the compact button's name (issue 692: "More tabs").
 */
export function RowMenu({
  name,
  items,
  compact = false,
  status,
  label,
}: {
  name: string;
  items: RowMenuItem[];
  compact?: boolean;
  status?: { label: string; tip: string; className: string };
  label?: string;
}) {
  const id = useId();
  const anchor = `--row-menu-${id.replace(/[^\w-]/g, "")}`;
  if (items.length === 0) return null;
  return (
    <>
      {compact ? (
        <IconButton
          icon="more"
          label={status?.label ?? label ?? `More actions for ${name}`}
          title={status?.tip}
          className={status ? `btn-quiet ${status.className}` : "btn-quiet"}
          popoverTarget={id}
          aria-haspopup="menu"
          style={{ anchorName: anchor }}
        />
      ) : (
        <button
          type="button"
          className={`btn btn-quiet ${styles.rowMenuButton}`}
          popoverTarget={id}
          aria-haspopup="menu"
          style={{ anchorName: anchor }}
        >
          {label ?? "More"}
          {label ? null : <span className="visually-hidden"> actions for {name}</span>}
          <Icon name="chevron-right" size={14} className={styles.rowMenuCaret} />
        </button>
      )}
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
        {items.map((item, index) => {
          // The line under the label: why it can't be used yet, else what it does. The label
          // names the item; the line is read as its description, cut to one line on screen
          // with its full text as the tooltip (#711).
          const hint = item.disabledReason || item.description;
          const hintId = `${id}-hint-${index}`;
          return (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className={item.destructive ? `${styles.rowMenuItem} ${styles.rowMenuDanger}` : styles.rowMenuItem}
              aria-label={hint ? item.label : undefined}
              aria-describedby={hint ? hintId : undefined}
              title={hint || undefined}
              aria-disabled={item.disabledReason ? true : undefined}
              onClick={(event) => {
                if (item.disabledReason) return;
                event.currentTarget.closest<HTMLElement>("[popover]")?.hidePopover();
                item.onSelect();
              }}
            >
              {item.label}
              {hint && (
                <span id={hintId} className={styles.rowMenuHint}>
                  {hint}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </>
  );
}
