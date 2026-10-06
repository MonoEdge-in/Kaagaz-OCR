"use client";

// /operator: three tabs, Organisations, Activity and Health, as the admin
// screen's tabs (AdminView): a tablist whose arrow keys, Home and End move
// between them. Only the open tab is mounted, so each loads when opened.

import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { tabAfterKey } from "@/components/admin/logic";
import { ActivityView } from "./ActivityView";
import { HealthView } from "./HealthView";
import { OrganisationsView } from "./OrganisationsView";
import admin from "@/components/admin/Admin.module.css";

export const OPERATOR_TABS = [
  { id: "orgs", label: "Organisations" },
  { id: "activity", label: "Activity" },
  { id: "health", label: "Health" },
] as const;

type TabId = (typeof OPERATOR_TABS)[number]["id"];

export function OperatorConsole() {
  const [active, setActive] = useState<TabId>("orgs");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = tabAfterKey(index, event.key, OPERATOR_TABS.length);
    const tab = next === null ? undefined : OPERATOR_TABS[next];
    if (next === null || !tab) return;
    event.preventDefault();
    setActive(tab.id);
    tabRefs.current[next]?.focus();
  }

  return (
    <div className="page">
      <h1>Platform</h1>
      <div role="tablist" aria-label="Platform sections" className={admin.tabs}>
        {OPERATOR_TABS.map((tab, index) => (
          <button
            key={tab.id}
            ref={(element) => {
              tabRefs.current[index] = element;
            }}
            type="button"
            role="tab"
            id={`operator-tab-${tab.id}`}
            aria-selected={tab.id === active}
            aria-controls={`operator-panel-${tab.id}`}
            tabIndex={tab.id === active ? 0 : -1}
            className={admin.tab}
            onClick={() => setActive(tab.id)}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {OPERATOR_TABS.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`operator-panel-${tab.id}`}
          aria-labelledby={`operator-tab-${tab.id}`}
          tabIndex={0}
          hidden={tab.id !== active}
          className={admin.panel}
        >
          {tab.id === active && tab.id === "orgs" && <OrganisationsView />}
          {tab.id === active && tab.id === "activity" && <ActivityView />}
          {tab.id === active && tab.id === "health" && <HealthView />}
        </div>
      ))}
    </div>
  );
}
