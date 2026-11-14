"use client";

// The admin screen: nine tabs (Users, Settings, Departments, AI settings, Activity, Numbers,
// Answer quality, Usage, Failed documents), one line that scrolls sideways (Tabs.tsx). The
// models are the operator's only (UI quality council, 15 Oct 2026). Only the open tab is mounted, so each one loads its data when it is opened. Settings
// also shows `twoStep`, the signed-in admin's own Two-step sign-in, which the
// page passes in (app/(app)/admin/TwoStepPanel.tsx).
//
// #468: with unsaved Settings or AI settings, another tab opens only once the admin says to leave
// without saving; their kept draft (lib/drafts.ts) goes then too.
//
// It opens only for an admin session (PRD A1; every admin's sign-in at /login
// gives one since 16 Oct 2026): an admin with an app session is sent to sign in,
// anyone else is told it is for admins. The link is shown
// to admin sessions only, and the server refuses the data to anyone else.

import Link from "next/link";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { EmptyState } from "@/components/ui/EmptyState";
import { clearDraft } from "@/lib/drafts";
import { useSession } from "@/lib/session";
import { leavePage } from "@/lib/sessionChannel";
import { ActivityPanel } from "./ActivityPanel";
import { AiSettingsPanel } from "./AiSettingsPanel";
import { AnswerQualityPanel } from "./AnswerQualityPanel";
import { DepartmentsPanel } from "./DepartmentsPanel";
import { FailedPanel } from "./FailedPanel";
import { LearnedList } from "./LearnedList";
import { LEAVE_UNSAVED, SETTINGS_DRAFT, TABS, adminGate, aiSettingsDraft, tabChangeAllowed, tabFrom } from "./logic";
import type { TabId } from "./logic";
import { NumbersPanel } from "./NumbersPanel";
import { SettingsPanel } from "./SettingsPanel";
import { Tabs } from "./Tabs";
import { UsagePanel } from "./UsagePanel";
import { UsersPanel } from "./UsersPanel";
import styles from "./Admin.module.css";

/** `tab`: the ?tab= the page was opened with (/admin?tab=departments, PRD 7 lead decision 12). */
export function AdminView({ twoStep, tab }: { twoStep: ReactNode; tab?: string }) {
  return (
    <AdminGate>
      <AdminTabs twoStep={twoStep} tab={tab} />
    </AdminGate>
  );
}

/** Anyone but an admin session sees this instead of the admin screens (the Access page too). */
export function AdminGate({ children }: { children: ReactNode }) {
  const { me } = useSession();
  const gate = adminGate(me);

  // A full page load: someone else may sign in there (critical #5).
  useEffect(() => {
    if (gate === "admin_sign_in") leavePage("/login?next=/admin");
  }, [gate]);

  if (gate === "no_access") {
    return (
      <div className="page">
        <EmptyState
          icon="admin"
          title="Admin is for your organisation's admins."
          headingLevel={1}
          action={
            <Link href="/documents" className="btn">
              Go to Documents
            </Link>
          }
        />
      </div>
    );
  }

  if (gate !== "allow") {
    return (
      <div className="page">
        <p className={`delayed ${styles.redirect}`} role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          Taking you to sign in…
        </p>
      </div>
    );
  }

  return children;
}

function AdminTabs({ twoStep, tab }: { twoStep: ReactNode; tab?: string }) {
  const { me } = useSession();
  const [active, setActive] = useState<TabId>(() => tabFrom(tab));
  const [unsaved, setUnsaved] = useState(false); // the open Settings or AI settings tab has changes not saved

  /** Opens the tab, and says whether it did: not when the admin stays with unsaved changes. */
  function openTab(next: TabId): boolean {
    if (next === active) return true;
    if (!tabChangeAllowed(unsaved, () => window.confirm(LEAVE_UNSAVED))) return false;
    if (unsaved) {
      // Only the draft of the tab being left: the other tab's unsaved changes stay kept.
      if (active === "settings") clearDraft(me.user.id, SETTINGS_DRAFT);
      if (active === "ai") clearDraft(me.user.id, aiSettingsDraft("organisation"));
    }
    setUnsaved(false);
    setActive(next);
    return true;
  }

  return (
    <div className="page">
      {/* No people count: it would need its own ask for the users list on every tab. */}
      <header className="page-header">
        <div>
          <h1>Admin</h1>
          {/* G12 (UI audit, 16 Oct 2026): the organisation's name with its label, never alone as a title. */}
          <p>
            Workspace: <strong>{me.customer.name}</strong>
          </p>
        </div>
      </header>
      <Tabs<TabId> prefix="admin" label="Admin sections" tabs={TABS} active={active} onChange={openTab}>
        {active === "users" && (
          <UsersPanel currentUserId={me.user.id} adminTwoStep={me.user.totp_enabled} providers={me.providers} />
        )}
        {active === "settings" && (
          <div className={styles.panelStack}>
            <SettingsPanel providers={me.providers} onUnsaved={setUnsaved} />
            {twoStep}
          </div>
        )}
        {active === "departments" && <DepartmentsPanel />}
        {active === "ai" && <AiSettingsPanel scope="organisation" onUnsaved={setUnsaved} />}
        {active === "activity" && <ActivityPanel />}
        {active === "numbers" && <NumbersPanel />}
        {active === "usage" && <UsagePanel />}
        {active === "failed" && <FailedPanel />}
        {active === "quality" && (
          <div className={styles.panelStack}>
            <AnswerQualityPanel />
            <LearnedList />
          </div>
        )}
      </Tabs>
    </div>
  );
}
