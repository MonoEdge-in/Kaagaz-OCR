"use client";

// Organisations, the first tab of /operator: every organisation with its
// status and counts only (never a document's or a person's name), a search by
// name, and Create organisation. Admins opens one organisation's admins
// (AdminsDialog); AI settings opens its scanning and answering settings, Ensemble and
// budget (OrgAiSettings, features 25-27); Feature switches its own switches (0039,
// OrgFeatureSwitchesDialog); Session timeout its own sign-in idle timeout (0040,
// OrgSessionTimeoutDialog). They sit in one Manage menu per row (ManageMenu,
// 16 Oct 2026). Each change opens a dialog (ConfirmDialog): disable, enable,
// delete (only a disabled organisation) and cancel the deletion; a row being
// deleted shows its date and the days left. Auto-filing (feature 28) is allowed or
// stopped per organisation, also in a ConfirmDialog. The table is the shared reflow
// (Table.module.css): cards below 48rem of room, a table from there.
// initialSearch (Needs attention on Overview) fills the search and focuses it.
// #457: a password sign-up waits as "Waiting for approval", with whether its admin's email
// is confirmed; Approve shows only once it is, as a button beside Manage and in its menu. A
// waiting sign-up can't be disabled. "Waiting for approval" filters to them.

import { useEffect, useRef, useState } from "react";
import { useMinuteNow } from "@/components/chat/hooks";
import table from "@/components/documents/Table.module.css";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { listOrgs } from "@/lib/api";
import { formatBytes, formatNumber } from "@/lib/format";
import type { Organisation } from "@/lib/types";
import { AdminsDialog } from "./AdminsDialog";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { autofileConfirm, orgConfirm, orgFocusId } from "./confirms";
import type { OrgChange } from "./confirms";
import { CreateDialog } from "./CreateDialog";
import { ManageMenu } from "./ManageMenu";
import { OrgFeatureSwitchesDialog } from "./FeatureSwitches";
import { OrgAiSettings, orgAiFocusId } from "./OrgAiSettings";
import { OrgModelRates } from "./OrgModelRates";
import { OrgSessionTimeoutDialog } from "./SessionTimeout";
import { afterDone, countText, deleteCountdown, filterOrgs, GRACE_DAYS, lastActivityText, operatorErrorMessage, orgStatus, searchSummary, userCounts, waitingOnly } from "./logic";
import { MoreRows, useRowCap } from "@/components/admin/RowCap";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

const SKELETON_ROWS = [0, 1, 2];

// The header row; Name has the room the short columns leave.
const COLUMNS: { label: string; className?: string }[] = [
  { label: "Name" },
  { label: "Status", className: styles.statusColumn },
  { label: "Users", className: styles.usersColumn },
  { label: "Documents", className: styles.documentsColumn },
  { label: "Pages", className: styles.pagesColumn },
  { label: "Uploaded size", className: styles.sizeColumn },
  { label: "Last activity", className: styles.activityColumn },
  { label: "Actions", className: styles.actionsColumn },
];

export function OrganisationsView({ initialSearch = "" }: { initialSearch?: string }) {
  const [attempt, setAttempt] = useState(0);
  const [orgs, setOrgs] = useState<Organisation[] | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [search, setSearch] = useState(initialSearch);
  const [searched, setSearched] = useState(initialSearch); // the initialSearch last applied
  const [query, setQuery] = useState(initialSearch); // the search, 200ms after the last key
  const [waiting, setWaiting] = useState(false); // only the sign-ups waiting for approval
  const cap = useRowCap();
  const searchRef = useRef<HTMLInputElement>(null);
  const [creating, setCreating] = useState(false);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [adminsOf, setAdminsOf] = useState<Organisation | null>(null);
  const [modelsOf, setModelsOf] = useState<Organisation | null>(null);
  const [ratesOf, setRatesOf] = useState<Organisation | null>(null);
  const [switchesOf, setSwitchesOf] = useState<Organisation | null>(null);
  const [timeoutOf, setTimeoutOf] = useState<Organisation | null>(null);
  const [done, setDone] = useState("");
  const now = useMinuteNow();

  useEffect(() => {
    let cancelled = false;
    listOrgs().then(
      (result) => {
        if (!cancelled) setOrgs(result.items);
      },
      (caught: unknown) => {
        if (!cancelled) setError({ attempt, message: operatorErrorMessage(caught) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  if (initialSearch !== searched) {
    setSearched(initialSearch);
    setSearch(initialSearch);
  }

  // The search box exists once the organisations are in.
  const loaded = orgs !== null;
  useEffect(() => {
    if (initialSearch && loaded) searchRef.current?.focus();
  }, [initialSearch, loaded]);

  // Debounced: the list filters once typing pauses, so each key stays quick.
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search), 200);
    return () => window.clearTimeout(timer);
  }, [search]);

  const failed = error !== null && error.attempt === attempt ? error.message : null;
  const reload = () => setAttempt((n) => n + 1);
  const shown = orgs ? waitingOnly(filterOrgs(orgs, query), waiting) : [];
  const create = (
    <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
      <Icon name="plus" />
      Create organisation
    </button>
  );

  return (
    <section className={admin.section} aria-labelledby="orgs-heading">
      <div className="page-header">
        <div>
          <h2 id="orgs-heading" className={admin.sectionHeading}>
            Organisations
          </h2>
          <p>Counts only. No document or member is shown here.</p>
        </div>
        {orgs && orgs.length > 0 && create}
      </div>
      <p className="visually-hidden" role="status">
        {done}
      </p>

      {failed && !orgs && (
        <div className={admin.errorBanner} role="alert">
          <Icon name="alert" className={admin.errorIcon} />
          <div className={admin.errorBody}>
            <p className={admin.errorTitle}>Could not load the organisations.</p>
            <p>{failed}</p>
          </div>
          <button type="button" className="btn" onClick={reload}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {!orgs && !failed && (
        <>
          <p className="visually-hidden" role="status">
            Loading the organisations…
          </p>
          <div className={`delayed ${admin.skeletons}`} aria-hidden="true">
            {SKELETON_ROWS.map((row) => (
              <span key={row} className={`skeleton ${admin.skeletonRow}`} />
            ))}
          </div>
        </>
      )}
      {orgs && orgs.length === 0 && (
        <EmptyState icon="admin" title="No organisations yet" action={create}>
          Create the first one with its first admin.
        </EmptyState>
      )}
      {orgs && orgs.length > 0 && (
        <>
          <div className={styles.toolbar}>
            <div className={`field ${styles.search}`}>
              <label htmlFor="org-search">Search by name</label>
              <input
                ref={searchRef}
                id="org-search"
                className="input"
                type="search"
                autoComplete="off"
                spellCheck={false}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                aria-describedby="org-search-summary"
              />
            </div>
            <label className={styles.waitingFilter}>
              <input type="checkbox" checked={waiting} onChange={(event) => setWaiting(event.target.checked)} />
              Waiting for approval
            </label>
            <p id="org-search-summary" className={styles.summary} role="status">
              {searchSummary(shown.length, orgs.length, query || (waiting ? "waiting" : ""))}
            </p>
          </div>
          {shown.length === 0 ? (
            <p className={admin.emptyLine}>
              {waiting ? "No organisation is waiting for approval." : "No organisation name has these letters."}
            </p>
          ) : (
            <div className={`${table.reflowWrap} ${admin.tableScroll}`}>
              <table className={`table ${table.reflow}`} role="table">
                <caption className="visually-hidden">Organisations, counts only</caption>
                <thead role="rowgroup">
                  <tr role="row">
                    {COLUMNS.map((column) => (
                      <th key={column.label} scope="col" role="columnheader" className={column.className}>
                        {column.label === "Actions" ? <span className="visually-hidden">Actions</span> : column.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody role="rowgroup">
                  {shown.slice(0, cap.limit).map((org) => (
                    <OrgRow
                      key={org.id}
                      org={org}
                      now={now}
                      onAdmins={() => setAdminsOf(org)}
                      onModels={() => setModelsOf(org)}
                      onModelRates={() => setRatesOf(org)}
                      onSwitches={() => setSwitchesOf(org)}
                      onTimeout={() => setTimeoutOf(org)}
                      onChange={(change) => setChanging(orgConfirm(org, change, Date.now()))}
                      onAutofile={() => setChanging(autofileConfirm(org, !org.autofile_granted))}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <MoreRows shown={Math.min(cap.limit, shown.length)} total={shown.length} what="organisations" onMore={cap.showMore} />
        </>
      )}

      {creating && (
        <CreateDialog
          onClose={() => setCreating(false)}
          onCreated={(message) => {
            setDone(message);
            reload();
          }}
        />
      )}
      {adminsOf && <AdminsDialog org={adminsOf} onClose={() => setAdminsOf(null)} />}
      {modelsOf && <OrgAiSettings org={modelsOf} onClose={() => setModelsOf(null)} onSaved={setDone} />}
      {ratesOf && <OrgModelRates org={ratesOf} onClose={() => setRatesOf(null)} onSaved={setDone} />}
      {switchesOf && <OrgFeatureSwitchesDialog org={switchesOf} onClose={() => setSwitchesOf(null)} />}
      {timeoutOf && <OrgSessionTimeoutDialog org={timeoutOf} onClose={() => setTimeoutOf(null)} />}
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging((open) => afterDone(open, changing));
            setDone(message);
            reload();
          }}
        />
      )}
    </section>
  );
}

/** What each Manage item does, in one plain line under it (plain words, 25 Oct 2026). */
export const MENU_HELP = {
  admins: "See this organisation's admins. Add one, or disable, unlock or reset one's two-step sign-in.",
  ai: "Choose which AI reads its scans and answers its questions, Ensemble and its monthly budget.",
  switches: "Turn features on or off for this organisation only.",
  timeout: "Choose how long its people can be idle before they are signed out.",
  limits: "Cap how many AI requests a minute it and each of its people may send.",
  allowAutofile: "Lets its admin turn on filing new uploads into folders by type.",
  stopAutofile: "Turns auto-filing off here. Files already filed stay where they are.",
  disable: "Signs all its people out now and stops its work until you enable it again.",
  enable: "Lets this organisation's people sign in again.",
  approve: "Lets this new organisation's admin sign in and start uploading.",
  delete: `Deletes it with all its people and documents after ${GRACE_DAYS} days. You can cancel until then.`,
  cancel: "Keeps it and everything in it. It stays disabled until you enable it.",
} as const;

// The changes each status allows, as the server: delete only a disabled one; Approve only a
// pending one whose admin confirmed their email (OrgRow), which can't be disabled.
const CHANGES: Record<Organisation["status"], { change: OrgChange; label: string; description: string; danger?: boolean }[]> = {
  active: [{ change: "disable", label: "Disable", description: MENU_HELP.disable, danger: true }],
  pending: [{ change: "approve", label: "Approve", description: MENU_HELP.approve }],
  disabled: [
    { change: "enable", label: "Enable", description: MENU_HELP.enable },
    { change: "delete", label: "Delete", description: MENU_HELP.delete, danger: true },
  ],
  deleting: [{ change: "cancel", label: "Cancel deletion", description: MENU_HELP.cancel }],
};

function OrgRow(props: {
  org: Organisation;
  now: number;
  onAdmins(): void;
  onModels(): void;
  onModelRates(): void;
  onSwitches(): void;
  onTimeout(): void;
  onChange(change: OrgChange): void;
  onAutofile(): void;
}) {
  const { org, now, onAdmins, onModels, onModelRates, onSwitches, onTimeout, onChange, onAutofile } = props;
  const status = orgStatus(org);
  const countdown = org.status === "deleting" ? deleteCountdown(org.delete_after, now) : null;
  const size = formatBytes(org.storage_bytes);
  return (
    <tr role="row">
      <td role="cell" className={styles.orgName}>
        <span id={orgFocusId(org.id)} tabIndex={-1} className={styles.orgNameText} title={org.name}>
          {org.name}
        </span>
      </td>
      <td role="cell" data-label="Status">
        <span className="status-chip" data-tone={status.tone}>
          {status.text}
        </span>
        {status.note && <span className={styles.statusNote}>{status.note}</span>}
        {org.autofile_granted && (
          <span className={styles.statusNote}>{org.autofile_on ? "Auto-filing on" : "Auto-filing allowed, off"}</span>
        )}
        {countdown && <span className={styles.statusNote}>{countdown}</span>}
      </td>
      <td role="cell" data-label="Users">
        <span className={styles.counts}>
          {userCounts(org.users).map((text) => (
            <span key={text}>{text}</span>
          ))}
        </span>
      </td>
      <td role="cell" data-label="Documents" className="tabular">
        {formatNumber(org.documents)}
        <span className={styles.extra}>
          {countText(org.pages, "page", "pages")} · {size}
        </span>
      </td>
      <td role="cell" data-label="Pages" className={`tabular ${styles.pagesColumn}`}>
        {formatNumber(org.pages)}
      </td>
      <td role="cell" data-label="Uploaded size" className={`tabular ${styles.sizeColumn}`}>
        {size}
      </td>
      <td role="cell" data-label="Last activity">
        <span className={styles.when}>{lastActivityText(org.last_activity_at, now)}</span>
      </td>
      <td role="cell">
        <div className={styles.orgActions}>
          {/* #457 review: a waiting sign-up whose admin confirmed their email shows Approve here
              too, so the operator need not open the menu to find it. */}
          {org.status === "pending" && org.admin_email_confirmed && (
            <button type="button" className="btn" aria-label={`Approve ${org.name}`} onClick={() => onChange("approve")}>
              Approve
            </button>
          )}
          {/* One menu (owner, 16 Oct 2026): the safe actions, then Disable, Enable, Delete
              or Cancel deletion after a separator. Each change opens its confirm. The
              button keeps orgAiFocusId, so closing AI settings comes back to it. */}
          <ManageMenu
            name={org.name}
            buttonId={orgAiFocusId(org.id)}
            safe={[
              { label: "Admins", description: MENU_HELP.admins, onSelect: onAdmins },
              ...(org.status === "deleting"
                ? []
                : [
                    { label: "AI settings", description: MENU_HELP.ai, onSelect: onModels },
                    { label: "Feature switches", description: MENU_HELP.switches, onSelect: onSwitches },
                    { label: "Session timeout", description: MENU_HELP.timeout, onSelect: onTimeout },
                    { label: "Request limits", description: MENU_HELP.limits, onSelect: onModelRates },
                    org.autofile_granted
                      ? { label: "Stop auto-filing", description: MENU_HELP.stopAutofile, onSelect: onAutofile }
                      : { label: "Allow auto-filing", description: MENU_HELP.allowAutofile, onSelect: onAutofile },
                  ]),
            ]}
            changes={CHANGES[org.status]
              .filter(({ change }) => change !== "approve" || org.admin_email_confirmed)
              .map(({ change, label, description, danger }) => ({
              label,
              description,
              danger,
              onSelect: () => onChange(change),
            }))}
          />
        </div>
      </td>
    </tr>
  );
}
