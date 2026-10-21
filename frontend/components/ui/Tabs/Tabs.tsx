"use client";

// Tabs (Components.html, 09): the active one in ink with a 2px accent rule. Each
// tab can carry a count, in review colours when it is work to do ("3 to
// check"). It follows the ARIA tabs pattern: only the selected tab is in the
// Tab order, the arrow keys, Home and End move between tabs and select them.
// The screen draws the panel with tabPanelIds(), so tab and panel name each other.

import { useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { cx } from "../cx";
import styles from "./Tabs.module.css";

export interface TabItem<T extends string> {
  id: T;
  label: ReactNode;
  count?: ReactNode;
  /** The count is work to do: review colours. */
  countTone?: "review";
}

export interface TabsProps<T extends string> {
  /** Names the tab list: "Document sections". */
  label: string;
  /** Unique on the page; prefixes the tab and panel ids. */
  idPrefix: string;
  tabs: TabItem<T>[];
  selected: T;
  onSelect(id: T): void;
}

/** The ids that tie tab `id` to its panel: <div role="tabpanel" id={panel} aria-labelledby={tab}>. */
export function tabPanelIds(idPrefix: string, id: string): { tab: string; panel: string } {
  return { tab: `${idPrefix}-${id}-tab`, panel: `${idPrefix}-${id}-panel` };
}

/** The tab a key moves to from `index`, or null for a key tabs don't use. */
export function tabAfterKey(key: string, index: number, count: number): number | null {
  if (key === "ArrowRight") return (index + 1) % count;
  if (key === "ArrowLeft") return (index - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return null;
}

export function Tabs<T extends string>({ label, idPrefix, tabs, selected, onSelect }: TabsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const onKeyDown = (index: number, event: KeyboardEvent<HTMLButtonElement>) => {
    const next = tabAfterKey(event.key, index, tabs.length);
    if (next === null) return;
    event.preventDefault();
    onSelect(tabs[next]!.id);
    refs.current[next]?.focus();
  };

  return (
    <div className={styles.tabs} role="tablist" aria-label={label}>
      {tabs.map((tab, index) => {
        const ids = tabPanelIds(idPrefix, tab.id);
        const on = tab.id === selected;
        return (
          <button
            key={tab.id}
            ref={(button) => {
              refs.current[index] = button;
            }}
            type="button"
            role="tab"
            id={ids.tab}
            aria-selected={on}
            aria-controls={ids.panel}
            tabIndex={on ? 0 : -1}
            className={styles.tab}
            onClick={() => onSelect(tab.id)}
            onKeyDown={(event) => onKeyDown(index, event)}
          >
            {tab.label}
            {tab.count !== undefined ? (
              <span className={cx(styles.count, tab.countTone === "review" && styles.countReview)}>{tab.count}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
