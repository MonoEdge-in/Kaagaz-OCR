"use client";

// The dashboard, the home screen after sign-in (File management D1, D2): a greeting and
// Upload; four tiles (Documents with "+N this week", Departments, New versions this month,
// Versions to confirm); documents by type as bars drawn to one scale (CSS widths, no chart
// library); one card per department with its file count, opening the folder, and a dashed
// "Not in a department" card when such files exist; the 10 newest documents and the last
// 20 activities. One request (GET /api/dashboard). Upload here works as on the documents
// screen (the header's Upload opens the same #upload-input), into the department picked.

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { TimelineRow } from "@/components/activity/Timeline";
import { ACTIVITY_TEXT } from "@/components/activity/words";
import { buildTree } from "@/components/documents/folderTree";
import { ALL_DOCUMENTS, libraryHref } from "@/components/documents/libraryQuery";
import { ACCEPT_ATTRIBUTE } from "@/components/documents/logic";
import { UploadStatus, useUploads } from "@/components/documents/UploadArea";
import { DepartmentPicker } from "@/components/files/DepartmentPicker";
import { departmentsOf, lastDepartment, rememberDepartment } from "@/components/files/departments";
import { LabelChip } from "@/components/files/LabelChip";
import { canReview } from "@/components/review/review";
import { Icon } from "@/components/ui/Icon";
import { ApiError, getDashboard, listFolders } from "@/lib/api";
import { docTypeLabel, formatNumber, formatRelative } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Dashboard as DashboardData, Folder } from "@/lib/types";
import { DASHBOARD_TEXT, barShares, filesText, greeting, thisWeekText } from "./words";
import styles from "./Dashboard.module.css";

export function Dashboard() {
  const { me } = useSession();
  const [loaded, setLoaded] = useState<{ data: DashboardData; at: number } | null>(null);
  const data = loaded?.data ?? null;
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [department, setDepartment] = useState<string | null | undefined>(undefined);
  const [trayOpen, setTrayOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const uploads = useUploads(() => setAttempt((n) => n + 1));
  const tree = useMemo(() => buildTree(folders), [folders]);
  const departments = useMemo(() => departmentsOf(folders), [folders]);
  const chosen = department === undefined ? lastDepartment(departments) : department;

  useEffect(() => {
    let cancelled = false;
    getDashboard().then(
      (reply) => {
        if (cancelled) return;
        setLoaded({ data: reply, at: Date.now() }); // when it came, for "2 hours ago"
        setError(null);
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught instanceof ApiError ? caught.message : DASHBOARD_TEXT.loadFailed);
      },
    );
    listFolders().then(
      (reply) => {
        if (!cancelled) setFolders(reply.items);
      },
      () => undefined, // the picker stays hidden; uploads go where they always went
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  function onChoose(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (files.length === 0) return;
    setTrayOpen(true);
    void uploads.addFiles(files, undefined, chosen ?? undefined);
  }

  function pick(id: string | null) {
    setDepartment(id);
    rememberDepartment(id);
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{greeting(me.user.display_name, new Date().getHours())}</h1>
          <p>{me.customer.name}</p>
        </div>
        <div className={styles.uploadRow}>
          <DepartmentPicker departments={departments} value={chosen} onChange={pick} />
          <button type="button" className="btn" onClick={() => inputRef.current?.click()}>
            <Icon name="upload" />
            {DASHBOARD_TEXT.upload}
          </button>
        </div>
      </header>
      <label htmlFor="upload-input" className="visually-hidden">
        Files to upload
      </label>
      <input
        ref={inputRef}
        id="upload-input"
        className="visually-hidden"
        type="file"
        multiple
        accept={ACCEPT_ATTRIBUTE}
        tabIndex={-1}
        onChange={onChoose}
      />

      {error !== null && data === null && (
        <div className={styles.note} role="alert">
          <p>{error}</p>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            {DASHBOARD_TEXT.tryAgain}
          </button>
        </div>
      )}
      {data === null && error === null && (
        <p className="muted" role="status">
          {DASHBOARD_TEXT.loading}
        </p>
      )}
      {loaded !== null && <DashboardBody data={loaded.data} now={loaded.at} />}

      <UploadStatus
        open={trayOpen}
        onOpenChange={setTrayOpen}
        entries={uploads.entries}
        views={uploads.views}
        stored={uploads.stored}
        tooMany={uploads.tooMany}
        onClearFinished={uploads.clearFinished}
        onRestore={(key, documentId, name) => void uploads.restore(key, documentId, name)}
        onRetry={(key) => void uploads.retry(key)}
        tree={tree}
        onUndone={() => setAttempt((n) => n + 1)}
        canReread={canReview(me.user.role)}
      />
    </div>
  );
}

/** Everything under the header, from the reply. Exported for the render test. */
export function DashboardBody({ data, now }: { data: DashboardData; now: number }) {
  const { tiles } = data;
  const shares = barShares(data.by_type.map((row) => row.count));
  const week = thisWeekText(tiles.documents_this_week);
  return (
    <>
      <section aria-label="Totals" className={styles.tiles}>
        <Tile label={DASHBOARD_TEXT.tiles.documents} value={tiles.documents} extra={week} href="/documents" />
        <Tile label={DASHBOARD_TEXT.tiles.departments} value={tiles.departments} />
        <Tile label={DASHBOARD_TEXT.tiles.newVersions} value={tiles.new_versions_this_month} />
        <Tile
          label={DASHBOARD_TEXT.tiles.toConfirm}
          value={tiles.versions_to_confirm}
          href={tiles.versions_to_confirm > 0 ? "/documents?newer=1" : undefined}
          extra={tiles.versions_to_confirm > 0 ? DASHBOARD_TEXT.toConfirmLink : null}
        />
      </section>

      <div className={styles.columns}>
        <section className={styles.card} aria-labelledby="by-type-heading">
          <h2 id="by-type-heading" className={styles.heading}>
            {DASHBOARD_TEXT.byType}
          </h2>
          {data.by_type.length === 0 ? (
            <p className="muted">{DASHBOARD_TEXT.byTypeEmpty}</p>
          ) : (
            <ul className={styles.bars}>
              {data.by_type.map((row, index) => (
                <li key={row.doc_type ?? "unknown"} className={styles.bar}>
                  <span className={styles.barName}>{docTypeLabel(row.doc_type)}</span>
                  <span className={styles.barTrack} aria-hidden="true">
                    <span className={styles.barFill} style={{ width: `${shares[index] ?? 0}%` }} />
                  </span>
                  <span className={`${styles.barCount} tabular`}>{formatNumber(row.count)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className={styles.card} aria-labelledby="departments-heading">
          <h2 id="departments-heading" className={styles.heading}>
            {DASHBOARD_TEXT.departments}
          </h2>
          {data.departments.length === 0 && data.not_in_department === 0 ? (
            <p className="muted">{DASHBOARD_TEXT.departmentsEmpty}</p>
          ) : (
            <ul className={styles.departments}>
              {data.departments.map((department) => (
                <li key={department.id}>
                  <Link
                    className={styles.department}
                    href={libraryHref({ ...ALL_DOCUMENTS, folderId: department.id, subfolders: true })}
                  >
                    <span className={styles.departmentName}>{department.name}</span>
                    <span className="muted tabular">{filesText(department.files)}</span>
                  </Link>
                </li>
              ))}
              {data.not_in_department > 0 && (
                <li>
                  <Link className={`${styles.department} ${styles.outside}`} href="/documents">
                    <span className={styles.departmentName}>{DASHBOARD_TEXT.notInDepartment}</span>
                    <span className="muted tabular">{filesText(data.not_in_department)}</span>
                    <span className={styles.hint}>{DASHBOARD_TEXT.notInDepartmentHint}</span>
                  </Link>
                </li>
              )}
            </ul>
          )}
        </section>
      </div>

      <div className={styles.columns}>
        <section className={styles.card} aria-labelledby="recent-heading">
          <h2 id="recent-heading" className={styles.heading}>
            {DASHBOARD_TEXT.recent}
          </h2>
          {data.recent.length === 0 ? (
            <p className="muted">{DASHBOARD_TEXT.recentEmpty}</p>
          ) : (
            <ul className={styles.recent}>
              {data.recent.map((document) => (
                <li key={document.id} className={styles.recentRow}>
                  <Link href={`/documents/${document.id}`} className={styles.recentName}>
                    {document.display_name}
                  </Link>
                  <span className={styles.recentMeta}>
                    <LabelChip label={document.sensitivity} />
                    <span className="mono">v{document.version_no}</span>
                    <span>{docTypeLabel(document.doc_type)}</span>
                    <time dateTime={document.created_at}>{formatRelative(document.created_at, now)}</time>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className={styles.card} aria-labelledby="activity-heading">
          <h2 id="activity-heading" className={styles.heading}>
            {DASHBOARD_TEXT.activity}
          </h2>
          {data.activity.length === 0 ? (
            <p className="muted">{ACTIVITY_TEXT.empty}</p>
          ) : (
            <ol className={styles.activity} aria-label={DASHBOARD_TEXT.activity}>
              {data.activity.map((entry) => (
                <TimelineRow key={entry.id} entry={entry} showDocument />
              ))}
            </ol>
          )}
        </section>
      </div>
    </>
  );
}

function Tile({
  label,
  value,
  extra = null,
  href,
}: {
  label: string;
  value: number;
  extra?: string | null;
  href?: string;
}) {
  const body = (
    <>
      <span className={styles.tileLabel}>{label}</span>
      <span className={`${styles.tileValue} tabular`}>{formatNumber(value)}</span>
      {extra && <span className={styles.tileExtra}>{extra}</span>}
    </>
  );
  return href ? (
    <Link href={href} className={`${styles.tile} ${styles.tileLink}`}>
      {body}
    </Link>
  ) : (
    <div className={styles.tile}>{body}</div>
  );
}
