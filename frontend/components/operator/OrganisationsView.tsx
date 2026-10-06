"use client";

// Organisations, the operator's home screen: every organisation with its
// status and counts only (never a document's or a person's name), a search by
// name, and Create organisation. Admins opens one organisation's admins
// (AdminsDialog). Each change opens a dialog (ConfirmDialog):
// disable, enable, delete (only a disabled organisation) and cancel the
// deletion; a row being deleted shows its date and the days left. The table is the shared reflow
// (Table.module.css): cards below 48rem of room, a table from there.

import { useEffect, useState } from "react";
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
import { orgConfirm } from "./confirms";
import type { OrgChange } from "./confirms";
import { CreateDialog } from "./CreateDialog";
import { countText, deleteCountdown, filterOrgs, lastActivityText, operatorErrorMessage, orgStatus, searchSummary, userCounts } from "./logic";
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

export function OrganisationsView() {
  const [attempt, setAttempt] = useState(0);
  const [orgs, setOrgs] = useState<Organisation[] | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [adminsOf, setAdminsOf] = useState<Organisation | null>(null);
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

  const failed = error !== null && error.attempt === attempt ? error.message : null;
  const reload = () => setAttempt((n) => n + 1);
  const shown = orgs ? filterOrgs(orgs, search) : [];
  const create = (
    <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
      <Icon name="plus" />
      Create organisation
    </button>
  );

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Organisations</h1>
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
            <p id="org-search-summary" className={styles.summary} role="status">
              {searchSummary(shown.length, orgs.length, search)}
            </p>
          </div>
          {shown.length === 0 ? (
            <p className={admin.emptyLine}>No organisation name has these letters.</p>
          ) : (
            <div className={table.reflowWrap}>
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
                  {shown.map((org) => (
                    <OrgRow
                      key={org.id}
                      org={org}
                      now={now}
                      onAdmins={() => setAdminsOf(org)}
                      onChange={(change) => setChanging(orgConfirm(org, change, Date.now()))}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
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
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging(null);
            setDone(message);
            reload();
          }}
        />
      )}
    </div>
  );
}

// The changes each status allows, as the server: delete only a disabled one.
const CHANGES: Record<Organisation["status"], { change: OrgChange; label: string; danger?: boolean }[]> = {
  active: [{ change: "disable", label: "Disable", danger: true }],
  disabled: [
    { change: "enable", label: "Enable" },
    { change: "delete", label: "Delete", danger: true },
  ],
  deleting: [{ change: "cancel", label: "Cancel deletion" }],
};

function OrgRow(props: { org: Organisation; now: number; onAdmins(): void; onChange(change: OrgChange): void }) {
  const { org, now, onAdmins, onChange } = props;
  const status = orgStatus(org);
  const countdown = org.status === "deleting" ? deleteCountdown(org.delete_after, now) : null;
  const size = formatBytes(org.storage_bytes);
  return (
    <tr role="row">
      <td role="cell" className={styles.orgName}>
        {org.name}
      </td>
      <td role="cell" data-label="Status">
        <span className="status-chip" data-tone={status.tone}>
          {status.text}
        </span>
        {status.note && <span className={styles.statusNote}>{status.note}</span>}
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
        {lastActivityText(org.last_activity_at, now)}
      </td>
      <td role="cell">
        <span className={styles.rowActions}>
          <button type="button" className="btn" onClick={onAdmins} aria-label={`Admins of ${org.name}`}>
            Admins
          </button>
          {CHANGES[org.status].map(({ change, label, danger }) => (
            <button
              key={change}
              type="button"
              className={danger ? "btn btn-danger" : "btn"}
              onClick={() => onChange(change)}
              aria-label={`${label} ${org.name}`}
            >
              {label}
            </button>
          ))}
        </span>
      </td>
    </tr>
  );
}
