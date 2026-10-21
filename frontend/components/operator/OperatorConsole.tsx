"use client";

// /operator: nine tabs, Overview (the default) and Quality (operator insights,
// council 11 Oct 2026), Organisations, Models (with Compare models under the
// catalogue), AI spend (model choice), Activity and Health, as the admin
// screen's tabs (Tabs.tsx): one line that scrolls sideways, arrow keys, Home and End
// move between them, and on a phone the seven tabs are one select. Only the open tab is mounted,
// so each loads when opened. Needs attention opens an organisation in
// Organisations (searched by its name, the search focused), or Health or Models.
// Feature switches (spec 17 Oct 2026) holds the platform switches and the test model;
// while a test model runs, its banner sits above the tabs on every tab. Timeout setup (0040)
// holds the sign-in idle timeout.

import { useEffect, useState } from "react";
import { Tabs, tabId, tabSelectId } from "@/components/admin/Tabs";
import { getTestModel } from "@/lib/api";
import type { TestModel } from "@/lib/types";
import { ActivityView } from "./ActivityView";
import { FeatureSwitchesView, TestModelBanner } from "./FeatureSwitches";
import { HealthView } from "./HealthView";
import { ModelBoard } from "./ModelBoard";
import { ModelsView } from "./ModelsView";
import { OrganisationsView } from "./OrganisationsView";
import { OverviewView } from "./OverviewView";
import { QualityView } from "./QualityView";
import { SessionTimeoutView } from "./SessionTimeout";
import { SpendView } from "./SpendView";
import css from "./Insights.module.css";

export const OPERATOR_TABS = [
  { id: "overview", label: "Overview" },
  { id: "quality", label: "Quality" },
  { id: "orgs", label: "Organisations" },
  { id: "models", label: "Models" },
  { id: "spend", label: "AI spend" },
  { id: "activity", label: "Activity" },
  { id: "health", label: "Health" },
  { id: "features", label: "Feature switches" },
  { id: "timeout", label: "Timeout setup" },
] as const;

type TabId = (typeof OPERATOR_TABS)[number]["id"];

export function OperatorConsole() {
  const [active, setActive] = useState<TabId>("overview");
  const [orgSearch, setOrgSearch] = useState(""); // set only by Needs attention
  const [testModel, setTestModel] = useState<TestModel | null>(null);
  const [testAsk, setTestAsk] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getTestModel().then(
      (result) => !cancelled && setTestModel(result),
      () => undefined, // no banner: the tab still says what it can
    );
    return () => {
      cancelled = true;
    };
  }, [testAsk]);
  const reloadTestModel = () => setTestAsk((n) => n + 1);

  function choose(id: TabId) {
    setActive(id);
    setOrgSearch("");
  }

  function openTab(id: "health" | "models") {
    choose(id);
    // The tab, or the select that stands for the tabs on a phone: whichever is shown.
    const shown = [tabId("operator", id), tabSelectId("operator")]
      .map((each) => document.getElementById(each))
      .find((element) => element !== null && element.getClientRects().length > 0);
    shown?.focus();
  }

  function openOrg(name: string) {
    setActive("orgs");
    setOrgSearch(name); // OrganisationsView searches for it and focuses its search box
  }

  return (
    <div className={`page ${css.console}`}>
      <h1>Platform</h1>
      <TestModelBanner testModel={testModel} onChanged={reloadTestModel} />
      <Tabs<TabId> prefix="operator" label="Platform sections" tabs={OPERATOR_TABS} active={active} onChange={choose}>
        {active === "overview" && <OverviewView openOrg={openOrg} openTab={openTab} />}
        {active === "quality" && <QualityView />}
        {active === "orgs" && <OrganisationsView initialSearch={orgSearch} />}
        {active === "models" && (
          <>
            <ModelsView />
            <ModelBoard />
          </>
        )}
        {active === "spend" && <SpendView />}
        {active === "activity" && <ActivityView />}
        {active === "health" && <HealthView />}
        {active === "features" && <FeatureSwitchesView testModel={testModel} onTestModel={reloadTestModel} />}
        {active === "timeout" && <SessionTimeoutView />}
      </Tabs>
    </div>
  );
}
