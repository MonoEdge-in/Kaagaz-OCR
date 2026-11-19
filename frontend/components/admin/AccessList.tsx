"use client";

// The Access list (#706; /access): every person with one line of access, their role and the Users
// tab's Access cell (Management, then their departments), Disabled for a turned-off account, and
// Access → to their Access page (/admin/people/{id}, AccessPage.tsx), where it is changed. Read
// from the Users tab's list (GET /admin/users). Then each private folder by its path, opening it in
// Documents (from GET /folders, as the Access page's Give access does), and a link to the
// Departments tab. Admin sessions only, as the Admin screen (AdminGate); the server refuses anyone else.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ACCESS_LIST } from "@/components/access/words";
import access from "@/components/access/Access.module.css";
import { buildTree, pathLabel } from "@/components/documents/folderTree";
import { roleName } from "@/components/shell/nav";
import { Icon } from "@/components/ui/Icon";
import { listFolders, listUsers } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import { useSession } from "@/lib/session";
import type { AdminUser, Folder } from "@/lib/types";
import { AdminGate } from "./AdminView";
import { AccessCell } from "./UsersPanel";
import styles from "./Admin.module.css";

export interface Loaded {
  users: AdminUser[];
  folders: Folder[];
}

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
  const [data, setData] = useState<Loaded | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const retry = useCallback(() => {
    setFailed(null);
    setAttempt((n) => n + 1);
  }, []);

  useEffect(() => {
    let live = true;
    Promise.all([listUsers(), listFolders()]).then(
      ([users, folders]) => live && setData({ users: users.items, folders: folders.items }),
      (caught: unknown) => live && setFailed(errorWords(caught)),
    );
    return () => {
      live = false;
    };
  }, [attempt]);

  return <AccessListView data={data} failed={failed} myId={me.user.id} onRetry={retry} />;
}

/** The page, for the render tests: `data` null while it loads; `failed` the words of a failed load. */
export function AccessListView({
  data,
  failed,
  myId,
  onRetry,
}: {
  data: Loaded | null;
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
      </p>

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
      {!data && !failed && (
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
      {data && <People users={data.users} myId={myId} />}
      {data && <PrivateFolders folders={data.folders} />}
    </div>
  );
}

function People({ users, myId }: { users: AdminUser[]; myId: string }) {
  return (
    <section className={styles.section} aria-labelledby="access-people">
      <h2 id="access-people" className={styles.sectionHeading}>
        {ACCESS_LIST.people}
      </h2>
      {users.length === 0 ? (
        <p className={styles.emptyLine}>
          {ACCESS_LIST.empty} <Link href="/admin?tab=users">{ACCESS_LIST.addPeople}</Link>
        </p>
      ) : (
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
                  {user.disabled && (
                    <span className="status-chip" data-tone="danger">
                      {ACCESS_LIST.disabled}
                    </span>
                  )}
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
    </section>
  );
}

/** Each private folder by its path, A to Z; a folder inside one is not private of its own. */
function PrivateFolders({ folders }: { folders: Folder[] }) {
  const tree = buildTree(folders);
  const rows = folders
    .filter((folder) => folder.private)
    .map((folder) => ({ id: folder.id, label: pathLabel(tree, folder.id) || folder.name }))
    .sort((a, b) => a.label.localeCompare(b.label));
  return (
    <section className={styles.section} aria-labelledby="access-private-folders">
      <h2 id="access-private-folders" className={styles.sectionHeading}>
        {ACCESS_LIST.privateFolders}
      </h2>
      {rows.length === 0 ? (
        <p className={styles.emptyLine}>{ACCESS_LIST.noPrivateFolders}</p>
      ) : (
        <ul className={access.list}>
          {rows.map((row) => (
            <li key={row.id} className={access.row}>
              <Link href={`/documents?folder=${row.id}`}>{row.label}</Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
