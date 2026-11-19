"use client";

// The Access list (#706; /access): every person with one line of access, their role and the Users
// tab's Access cell (Management, then their departments), and Access → to their Access page
// (/admin/people/{id}, AccessPage.tsx), where it is changed. Read from the Users tab's list
// (GET /admin/users). Links to the Departments tab and to Documents, where a folder is made
// private. Admin sessions only, as the Admin screen (AdminGate); the server refuses anyone else.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ACCESS_LIST } from "@/components/access/words";
import access from "@/components/access/Access.module.css";
import { roleName } from "@/components/shell/nav";
import { Icon } from "@/components/ui/Icon";
import { listUsers } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import { useSession } from "@/lib/session";
import type { AdminUser } from "@/lib/types";
import { AdminGate } from "./AdminView";
import { AccessCell } from "./UsersPanel";
import styles from "./Admin.module.css";

export function AccessList() {
  return (
    <AdminGate>
      <AccessLoader />
    </AdminGate>
  );
}

function AccessLoader() {
  const { me } = useSession();
  const [attempt, setAttempt] = useState(0);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const retry = useCallback(() => {
    setFailed(null);
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    let live = true;
    listUsers().then(
      (reply) => live && setUsers(reply.items),
      (caught: unknown) => live && setFailed(errorWords(caught)),
    );
    return () => {
      live = false;
    };
  }, [attempt]);

  return <AccessListView users={users} failed={failed} myId={me.user.id} onRetry={retry} />;
}

/** The page, for the render tests: `users` null while it loads; `failed` the words of a failed load. */
export function AccessListView({
  users,
  failed,
  myId,
  onRetry,
}: {
  users: AdminUser[] | null;
  failed: string | null;
  myId: string;
  onRetry(): void;
}) {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{ACCESS_LIST.title}</h1>
          <p>{ACCESS_LIST.intro}</p>
        </div>
      </header>
      <p className={access.line}>
        <Link href="/admin?tab=departments">{ACCESS_LIST.departments}</Link>
        <Link href="/documents">{ACCESS_LIST.privateFolders}</Link>
      </p>
      <p className="hint">{ACCESS_LIST.privateFoldersHint}</p>

      {failed && (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>{ACCESS_LIST.loadFailed}</p>
            <p>{failed}</p>
          </div>
          <button type="button" className="btn" onClick={onRetry}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {!users && !failed && (
        <>
          <p className="visually-hidden" role="status">
            {ACCESS_LIST.loading}
          </p>
          <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
            {[0, 1, 2].map((row) => (
              <span key={row} className={`skeleton ${styles.skeletonRow}`} />
            ))}
          </div>
        </>
      )}
      {users && users.length === 0 && (
        <p className={styles.emptyLine}>
          {ACCESS_LIST.empty} <Link href="/admin?tab=users">{ACCESS_LIST.addPeople}</Link>
        </p>
      )}
      {users && users.length > 0 && (
        <ul className={access.list}>
          {users.map((user) => (
            <li key={user.id} className={access.row}>
              <span className={access.rowText}>
                <span>
                  {user.display_name}
                  {user.id === myId && <span className="muted"> (you)</span>}
                </span>
                <span className={`${access.rowLine} ${styles.statusList}`}>
                  <span>{roleName(user.role)}</span>
                  <span aria-hidden="true">·</span>
                  <AccessCell user={user} />
                </span>
              </span>
              <Link href={`/admin/people/${user.id}`} className="btn">
                {ACCESS_LIST.open}
                <span className="visually-hidden">{` for ${user.display_name}`}</span>
                <span aria-hidden="true"> →</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
