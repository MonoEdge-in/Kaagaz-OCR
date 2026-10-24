"use client";

// The admin screen: nine tabs (Users, Settings, Departments, AI settings, Activity, Numbers,
// Answer quality, Usage, Failed documents), one line that scrolls sideways (Tabs.tsx). The
// models are the operator's only (UI quality council, 15 Oct 2026). Only the open tab is mounted, so each one loads its data when it is opened. Settings
// also shows `twoStep`, the signed-in admin's own Two-step sign-in, which the
// page passes in (app/(app)/admin/TwoStepPanel.tsx).
//
// It opens only for an admin session (PRD A1; every admin's sign-in at /login
// gives one since 16 Oct 2026): an admin with an app session is sent to sign in,
// anyone else is told it is for admins. The link is shown
// to admin sessions only, and the server refuses the data to anyone else.

import Link from "next/link";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { EmptyState } from "@/components/ui/EmptyState";
import { useSession } from "@/lib/session";
import { leavePage } from "@/lib/sessionChannel";
import { ActivityPanel } from "./ActivityPanel";
import { AiSettingsPanel } from "./AiSettingsPanel";
import { AnswerQualityPanel } from "./AnswerQualityPanel";
import { DepartmentsPanel } from "./DepartmentsPanel";
import { FailedPanel } from "./FailedPanel";
import { LearnedList } from "./LearnedList";
import { TABS, adminGate } from "./logic";
import type { TabId } from "./logic";
import { NumbersPanel } from "./NumbersPanel";
import { SettingsPanel } from "./SettingsPanel";
import { Tabs } from "./Tabs";
import { UsagePanel } from "./UsagePanel";
import { UsersPanel } from "./UsersPanel";
import styles from "./Admin.module.css";

export function AdminView({ twoStep }: { twoStep: ReactNode }) {
  const { me } = useSession();
  const gate = adminGate(me);
  const [active, setActive] = useState<TabId>("users");

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
      <Tabs<TabId> prefix="admin" label="Admin sections" tabs={TABS} active={active} onChange={setActive}>
        {active === "users" && (
          <UsersPanel currentUserId={me.user.id} adminTwoStep={me.user.totp_enabled} />
        )}
        {active === "settings" && (
          <div className={styles.panelStack}>
            <SettingsPanel />
            {twoStep}
          </div>
        )}
        {active === "departments" && <DepartmentsPanel />}
        {active === "ai" && <AiSettingsPanel scope="organisation" />}
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
