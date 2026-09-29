"use client";

// The admin screen: four tabs (Users, Settings, Activity, Numbers) as a
// segmented control that scrolls sideways on its own at small widths. Only the
// open tab is mounted, so each one loads its data when it is opened.
//
// It opens only for an admin session (PRD A1): anyone else, including an admin
// who signed in at /login, is sent to /admin/login. The link is shown to admin
// sessions only, and the server refuses the data to anyone else.

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useSession } from "@/lib/session";
import { ActivityPanel } from "./ActivityPanel";
import { TABS, adminGate, tabAfterKey } from "./logic";
import type { TabId } from "./logic";
import { NumbersPanel } from "./NumbersPanel";
import { SettingsPanel } from "./SettingsPanel";
import { UsersPanel } from "./UsersPanel";
import styles from "./Admin.module.css";

export function AdminView() {
  const { me } = useSession();
  const router = useRouter();
  const gate = adminGate(me);
  const [active, setActive] = useState<TabId>("users");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    if (gate === "admin_sign_in") router.replace("/admin/login");
  }, [gate, router]);

  if (gate !== "allow") {
    return (
      <div className="page">
        <p className={`delayed ${styles.redirect}`} role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          Taking you to admin sign-in…
        </p>
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
    <div className="page">
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
              className={styles.tab}
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
