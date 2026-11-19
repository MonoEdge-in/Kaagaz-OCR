"use client";

// My access (#707; /my-access): what the signed-in person can see in Kaagaz, and why, from GET
// /me/access. The Access page's sections (components/admin/AccessPage.tsx), read only: no Give
// access, Remove or Management buttons. Nothing kept from the person is named or counted; one line
// says some files are kept to other people.

import Link from "next/link";
import { useEffect, useState } from "react";
import { MY_ACCESS, givenWhy, memberWhy } from "@/components/access/words";
import access from "@/components/access/Access.module.css";
import { LoadFailed, Section } from "@/components/admin/AccessPage";
import styles from "@/components/admin/Admin.module.css";
import { getMyAccess } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import { formatDate } from "@/lib/format";
import type { MyAccess as MyAccessReply } from "@/lib/types";

export function MyAccess() {
  const [attempt, setAttempt] = useState(0);
  const [data, setData] = useState<MyAccessReply | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getMyAccess().then(
      (found) => {
        if (!live) return;
        setData(found);
        setFailed(null);
      },
      (caught: unknown) => {
        if (live) setFailed(errorWords(caught));
      },
    );
    return () => {
      live = false;
    };
  }, [attempt]);

  return <MyAccessView data={data} failed={failed} onRetry={() => setAttempt((n) => n + 1)} />;
}

/** The page, for the render tests: `data` null while it loads; `failed` the words of a failed load. */
export function MyAccessView({ data, failed, onRetry }: { data: MyAccessReply | null; failed: string | null; onRetry(): void }) {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{MY_ACCESS.title}</h1>
          <p>{MY_ACCESS.intro}</p>
        </div>
      </header>
      {failed && <LoadFailed failed={failed} onRetry={onRetry} title={MY_ACCESS.loadFailed} />}
      {!data && !failed && (
        <>
          <p className="visually-hidden" role="status">
            {MY_ACCESS.loading}
          </p>
          <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
            {[0, 1, 2].map((row) => (
              <span key={row} className={`skeleton ${styles.skeletonRow}`} />
            ))}
          </div>
        </>
      )}
      {data && (
        <>
          {data.management && <p className={access.line}>{MY_ACCESS.management}</p>}

          <Section title={MY_ACCESS.departments} empty={data.departments.length === 0 ? MY_ACCESS.noDepartments : null} onGive={null} busy={false}>
            {data.departments.map((department) => (
              <li key={department.id} className={access.row}>
                <span className={access.rowText}>
                  <span>{department.name}</span>
                  <span className={access.rowLine}>{memberWhy(department.name, department.department_only)}</span>
                  {department.head && <span className={access.rowLine}>{MY_ACCESS.head}</span>}
                </span>
              </li>
            ))}
          </Section>

          <Section title={MY_ACCESS.privateFolders} empty={data.private_folders.length === 0 ? MY_ACCESS.noFolders : null} onGive={null} busy={false}>
            {data.private_folders.map((folder) => (
              <li key={folder.id} className={access.row}>
                <span className={access.rowText}>
                  <span>{folder.path ?? folder.name}</span>
                  <span className={access.rowLine}>{MY_ACCESS.named}</span>
                </span>
              </li>
            ))}
          </Section>

          <Section title={MY_ACCESS.files} empty={data.files.length === 0 ? MY_ACCESS.noFiles : null} onGive={null} busy={false}>
            {data.files.map((file) => (
              <li key={file.id} className={access.row}>
                <span className={access.rowText}>
                  <Link href={`/documents/${encodeURIComponent(file.id)}`}>{file.name}</Link>
                  {file.path && <span className={access.rowLine}>{file.path}</span>}
                  <span className={access.rowLine}>{givenWhy(formatDate(file.given_at), file.given_by)}</span>
                </span>
              </li>
            ))}
          </Section>

          {data.walled && !data.management && <p className="hint">{MY_ACCESS.kept}</p>}
        </>
      )}
    </div>
  );
}
