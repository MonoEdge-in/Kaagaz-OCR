"use client";

// The admin screen: four tabs (Users, Settings, Activity, Numbers). Only the
// open tab is mounted, so each one loads its data when it is opened. The link is
// shown to admins only, and the server refuses the data to anyone else.

import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useSession } from "@/lib/session";
import { ActivityPanel } from "./ActivityPanel";
import { TABS, tabAfterKey } from "./logic";
import type { TabId } from "./logic";
import { NumbersPanel } from "./NumbersPanel";
import { SettingsPanel } from "./SettingsPanel";
import { UsersPanel } from "./UsersPanel";
import styles from "./Admin.module.css";

export function AdminView() {
  const { me } = useSession();
  const [active, setActive] = useState<TabId>("users");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  if (me.user.role !== "admin") {
    return (
      <div className="stack">
        <h1>Admin</h1>
        <p className="card">Only an admin can use this page.</p>
      </div>
    );
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = tabAfterKey(index, event.key, TABS.length);
    const tab = next === null ? undefined : TABS[next];
    if (next === null || !tab) return;
    event.preventDefault();
    setActive(tab.id);
    tabRefs.current[next]?.focus();
  }

  return (
    <div className="stack">
      <h1>Admin</h1>
      <div role="tablist" aria-label="Admin sections" className={styles.tabs}>
        {TABS.map((tab, index) => {
          const selected = tab.id === active;
          return (
            <button
              key={tab.id}
              ref={(element) => {
                tabRefs.current[index] = element;
              }}
              type="button"
              role="tab"
              id={`admin-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`admin-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              className={selected ? `${styles.tab} ${styles.tabActive}` : styles.tab}
              onClick={() => setActive(tab.id)}
              onKeyDown={(event) => onKeyDown(event, index)}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      {TABS.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`admin-panel-${tab.id}`}
          aria-labelledby={`admin-tab-${tab.id}`}
          tabIndex={0}
          hidden={tab.id !== active}
          className={styles.panel}
        >
          {tab.id === active && tab.id === "users" && <UsersPanel currentUserId={me.user.id} />}
          {tab.id === active && tab.id === "settings" && <SettingsPanel />}
          {tab.id === active && tab.id === "activity" && <ActivityPanel />}
          {tab.id === active && tab.id === "numbers" && <NumbersPanel />}
        </div>
      ))}
    </div>
  );
}
