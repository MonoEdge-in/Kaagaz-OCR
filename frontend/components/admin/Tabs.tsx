"use client";

// The admin and operator screens' tabs (MonoEdge design system, 16 Oct 2026): one line
// of tabs that scrolls sideways and never wraps, the selected one kept in view, with
// edge arrows where there is a mouse. Arrow keys, Home and End move between the tabs
// (roving tabindex: only the selected tab is in the Tab order). More than six tabs
// become one select below 768px. Every panel keeps the same least height, so switching
// never moves the page. Only the open panel's content is mounted (`children`).

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { Icon } from "@/components/ui/Icon";
import { formatNumber } from "@/lib/format";
import { useTabMotion } from "@/lib/motion";
import { tabAfterKey } from "./logic";
import styles from "./Admin.module.css";

export interface TabItem<T extends string> {
  id: T;
  label: string;
  count?: number;
}

/** More tabs than this become a select on a phone. */
export const MAX_PHONE_TABS = 6;

export const tabId = (prefix: string, id: string) => `${prefix}-tab-${id}`;
export const panelId = (prefix: string, id: string) => `${prefix}-panel-${id}`;
export const tabSelectId = (prefix: string) => `${prefix}-tab-select`;

export function Tabs<T extends string>(props: {
  /** "admin" gives admin-tab-users and admin-panel-users. */
  prefix: string;
  label: string;
  tabs: readonly TabItem<T>[];
  active: T;
  /** Open this tab; false when it did not open (#468: the admin chose to stay), so focus stays put. */
  onChange(id: T): boolean;
  children: ReactNode;
}) {
  const { prefix, label, tabs, active, onChange, children } = props;
  const listRef = useRef<HTMLDivElement>(null);
  useTabMotion(listRef, active);
  const [edges, setEdges] = useState({ start: false, end: false });
  const many = tabs.length > MAX_PHONE_TABS;

  // Which sides have more tabs, on scroll (passive) and on resize (ResizeObserver).
  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const update = () => {
      const left = Math.abs(list.scrollLeft);
      const start = left > 1;
      const end = left + list.clientWidth < list.scrollWidth - 1;
      setEdges((was) => (was.start === start && was.end === end ? was : { start, end }));
    };
    update();
    list.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(list);
    return () => {
      list.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, []);

  // The selected tab scrolled into view, sideways only: the page itself never moves.
  useEffect(() => {
    const list = listRef.current;
    const tab = list?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!list || !tab) return;
    if (tab.offsetLeft < list.scrollLeft) list.scrollLeft = tab.offsetLeft;
    else if (tab.offsetLeft + tab.offsetWidth > list.scrollLeft + list.clientWidth) {
      list.scrollLeft = tab.offsetLeft + tab.offsetWidth - list.clientWidth;
    }
  }, [active]);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = tabAfterKey(index, event.key, tabs.length);
    const tab = next === null ? undefined : tabs[next];
    if (!tab) return;
    event.preventDefault();
    if (onChange(tab.id)) document.getElementById(tabId(prefix, tab.id))?.focus();
  }

  const scrollBy = (side: -1 | 1) => listRef.current?.scrollBy({ left: side * listRef.current.clientWidth * 0.8 });

  return (
    <>
      {many && (
        <div className={`field ${styles.tabSelect}`}>
          <label htmlFor={tabSelectId(prefix)}>{label}</label>
          <select
            id={tabSelectId(prefix)}
            className="input"
            value={active}
            aria-controls={panelId(prefix, active)}
            onChange={(event) => {
              const tab = tabs.find((each) => each.id === event.target.value);
              if (tab) onChange(tab.id);
            }}
          >
            {tabs.map((tab) => (
              <option key={tab.id} value={tab.id}>
                {tab.count === undefined ? tab.label : `${tab.label} (${formatNumber(tab.count)})`}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className={styles.tabBar} data-many={many || undefined}>
        {/* Mouse only (keys move with the arrows): hidden from screen readers and the Tab order. */}
        <button
          type="button"
          className={styles.tabEdge}
          data-side="start"
          tabIndex={-1}
          aria-hidden="true"
          hidden={!edges.start}
          onClick={() => scrollBy(-1)}
        >
          <Icon name="chevron-left" size={16} />
        </button>
        <div ref={listRef} role="tablist" aria-label={label} className={styles.tabs}>
          {tabs.map((tab, index) => {
            const selected = tab.id === active;
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                id={tabId(prefix, tab.id)}
                aria-selected={selected}
                aria-controls={panelId(prefix, tab.id)}
                tabIndex={selected ? 0 : -1}
                className={styles.tab}
                onClick={() => onChange(tab.id)}
                onKeyDown={(event) => onKeyDown(event, index)}
              >
                <span className={styles.tabLabel} data-label={tab.label}>
                  {tab.label}
                </span>
                {tab.count !== undefined && <span className={styles.tabCount}>{formatNumber(tab.count)}</span>}
              </button>
            );
          })}
        </div>
        <button
          type="button"
          className={styles.tabEdge}
          data-side="end"
          tabIndex={-1}
          aria-hidden="true"
          hidden={!edges.end}
          onClick={() => scrollBy(1)}
        >
          <Icon name="chevron-right" size={16} />
        </button>
      </div>
      {tabs.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={panelId(prefix, tab.id)}
          aria-labelledby={tabId(prefix, tab.id)}
          tabIndex={0}
          hidden={tab.id !== active}
          className={styles.panel}
        >
          {tab.id === active && children}
        </div>
      ))}
    </>
  );
}
