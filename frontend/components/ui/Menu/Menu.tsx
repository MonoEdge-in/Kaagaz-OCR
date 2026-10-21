"use client";

// A menu (Components.html, 12, the account menu): an optional header (who is
// signed in), rows with a control of their own (Theme), then the items. Only
// the items are in the role="menu" list, so a screen reader hears a clean
// list of actions; the arrow keys, Home and End move between them. Opening it
// from a button belongs to the screen that has one (the app shell).

import { useRef } from "react";
import type { HTMLAttributes, KeyboardEvent, ReactNode } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./Menu.module.css";

export function Menu({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div {...rest} className={cx(styles.menu, className)} />;
}

export function MenuHeader({ title, sub }: { title: ReactNode; sub?: ReactNode }) {
  return (
    <div className={styles.header}>
      <strong>{title}</strong>
      {sub ? <span className="k-small k-muted">{sub}</span> : null}
    </div>
  );
}

/** A row with its own control: "Theme" and the Light / Dark / System switch. */
export function MenuRow({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className={styles.row}>
      <span className="k-small k-muted">{label}</span>
      {children}
    </div>
  );
}

/** The item a key moves focus to from `index`, or null for a key the menu doesn't use. */
export function itemAfterKey(key: string, index: number, count: number): number | null {
  if (key === "ArrowDown") return (index + 1) % count;
  if (key === "ArrowUp") return (index - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

export function MenuItems({ label, children }: { label: string; children: ReactNode }) {
  const list = useRef<HTMLDivElement>(null);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(list.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
    const next = itemAfterKey(event.key, items.indexOf(event.target as HTMLElement), items.length);
    if (next === null || items.length === 0) return;
    event.preventDefault();
    items[next]?.focus();
  };
  return (
    <div ref={list} className={styles.items} role="menu" aria-label={label} onKeyDown={onKeyDown}>
      {children}
    </div>
  );
}

export interface MenuItemProps {
  icon?: KIconName;
  danger?: boolean;
  onSelect(): void;
  children: ReactNode;
}

export function MenuItem({ icon, danger, onSelect, children }: MenuItemProps) {
  return (
    <button type="button" role="menuitem" className={cx(styles.item, danger && styles.danger)} onClick={onSelect}>
      {icon ? <Icon name={icon} /> : null}
      {children}
    </button>
  );
}
