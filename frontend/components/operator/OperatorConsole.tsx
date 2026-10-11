"use client";

// /operator: seven tabs, Overview (the default) and Quality (operator insights,
// council 11 Oct 2026), Organisations, Models (with Compare models under the
// catalogue), AI spend (model choice), Activity and Health, as the admin
// screen's tabs (AdminView): a tablist whose arrow keys, Home and End move
// between them; below about 40rem the tabs wrap. Only the open tab is mounted,
// so each loads when opened. Needs attention opens an organisation in
// Organisations (searched by its name, the search focused), or Health or Models.

import { useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { tabAfterKey } from "@/components/admin/logic";
import { ActivityView } from "./ActivityView";
import { HealthView } from "./HealthView";
import { ModelBoard } from "./ModelBoard";
import { ModelsView } from "./ModelsView";
import { OrganisationsView } from "./OrganisationsView";
import { OverviewView } from "./OverviewView";
import { QualityView } from "./QualityView";
import { SpendView } from "./SpendView";
import admin from "@/components/admin/Admin.module.css";
import css from "./Insights.module.css";

export const OPERATOR_TABS = [
  { id: "overview", label: "Overview" },
  { id: "quality", label: "Quality" },
  { id: "orgs", label: "Organisations" },
  { id: "models", label: "Models" },
  { id: "spend", label: "AI spend" },
  { id: "activity", label: "Activity" },
  { id: "health", label: "Health" },
] as const;

type TabId = (typeof OPERATOR_TABS)[number]["id"];

export function OperatorConsole() {
  const [active, setActive] = useState<TabId>("overview");
  const [orgSearch, setOrgSearch] = useState(""); // set only by Needs attention
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  function choose(id: TabId) {
    setActive(id);
    setOrgSearch("");
  }

  function openTab(id: "health" | "models") {
    choose(id);
    tabRefs.current[OPERATOR_TABS.findIndex((tab) => tab.id === id)]?.focus();
  }

  function openOrg(name: string) {
    setActive("orgs");
    setOrgSearch(name); // OrganisationsView searches for it and focuses its search box
  }

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const next = tabAfterKey(index, event.key, OPERATOR_TABS.length);
    const tab = next === null ? undefined : OPERATOR_TABS[next];
    if (next === null || !tab) return;
    event.preventDefault();
    choose(tab.id);
    tabRefs.current[next]?.focus();
  }

  return (
    <div className="page">
      <h1>Platform</h1>
      <div role="tablist" aria-label="Platform sections" className={`${admin.tabs} ${css.tabsWrap}`}>
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
            onClick={() => choose(tab.id)}
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
          {tab.id === active && tab.id === "overview" && <OverviewView openOrg={openOrg} openTab={openTab} />}
          {tab.id === active && tab.id === "quality" && <QualityView />}
          {tab.id === active && tab.id === "orgs" && <OrganisationsView initialSearch={orgSearch} />}
          {tab.id === active && tab.id === "models" && (
            <>
              <ModelsView />
              <ModelBoard />
            </>
          )}
          {tab.id === active && tab.id === "spend" && <SpendView />}
          {tab.id === active && tab.id === "activity" && <ActivityView />}
          {tab.id === active && tab.id === "health" && <HealthView />}
        </div>
      ))}
    </div>
  );
}
