"use client";

// The dashboard, the home screen after sign-in (File management D1, D2), in the handoff's
// layout (design/kaagaz-ui-handoff/screens/desktop/Home.html): on a dot field, the day,
// the greeting with what waits for this person, the Ask box and three questions to ask
// with one press; beside it the Review queue (a reviewer's and an admin's, D-7) or, for a
// member, their library, and Upload under it. Then the four totals (Documents with "+N this
// week", Departments, New versions this month, Versions to confirm), documents by type as
// bars on one scale, one tile per department and a dashed "Not in a department", the
// newest documents and the last 20 activities. One request (GET /api/dashboard), plus the
// Review total for a reviewer. Upload works as on the documents screen (the top bar's
// Upload opens the same #upload-input), into the department picked; the files join the
// shell's one queue and tray (components/shell/Uploads.tsx, D-12). What the backend cannot
// say yet is left out: review counts per department (NB-06), "updated today" (NB-08),
// versions linked by people or on their own (NB-07) and the folder of a recent document (NB-09).

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent } from "react";
import { activityDocument, activitySentence } from "@/components/activity/words";
import { askHref } from "@/components/chat/logic";
import { ALL_DOCUMENTS, libraryHref } from "@/components/documents/libraryQuery";
import { ACCEPT_ATTRIBUTE } from "@/components/documents/logic";
import { DepartmentPicker } from "@/components/files/DepartmentPicker";
import { departmentsOf, lastDepartment, rememberDepartment } from "@/components/files/departments";
import { canReview } from "@/components/review/review";
import { useSharedUploads } from "@/components/shell/Uploads";
import { Icon } from "@/components/ui/Icon/Icon";
import type { KIconName } from "@/components/ui/Icon/Icon";
import { LabelChip } from "@/components/ui/LabelChip/LabelChip";
import { ApiError, getDashboard, listFolders, listReview } from "@/lib/api";
import { docTypeLabel, formatDateTime, formatNumber, formatRelative } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ActivityEntry, Dashboard as DashboardData, Folder } from "@/lib/types";
import {
  DASHBOARD_TEXT,
  barShares,
  extensionOf,
  filesText,
  greeting,
  suggestedQuestions,
  thisWeekText,
  todayLine,
  waitingText,
} from "./words";
import styles from "./Dashboard.module.css";

export function Dashboard() {
  const { me } = useSession();
  const router = useRouter();
  const reviewer = canReview(me.user.role);
  const [loaded, setLoaded] = useState<{ data: DashboardData; at: number } | null>(null);
  const data = loaded?.data ?? null;
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [reviewTotal, setReviewTotal] = useState<number | null>(null);
  const [department, setDepartment] = useState<string | null | undefined>(undefined);
  const [question, setQuestion] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const { uploads, setTrayOpen } = useSharedUploads(() => setAttempt((n) => n + 1));
  const departments = useMemo(() => departmentsOf(folders), [folders]);
  const chosen = department === undefined ? lastDepartment(departments) : department;
  const chosenName = departments.find((folder) => folder.id === chosen)?.name ?? null;

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
    if (reviewer) {
      listReview({ limit: 1 }).then(
        (reply) => {
          if (!cancelled) setReviewTotal(reply.total);
        },
        () => undefined, // the greeting leaves the values out rather than guess
      );
    }
    return () => {
      cancelled = true;
    };
  }, [attempt, reviewer]);

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

  function ask(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (text) router.push(askHref(text));
  }

  const versions = data?.tiles.versions_to_confirm ?? 0;
  const values = reviewer ? reviewTotal : null;
  const suggestions = data ? suggestedQuestions(data.by_type) : [];

  return (
    <div className={`page ${styles.page}`}>
      <section className={styles.hero} aria-labelledby="home-greeting">
        <div className={`k-dotfield ${styles.welcome}`}>
          <span className="k-tagline">
            <b>01</b>
            <i />
            {todayLine(new Date(), me.customer.name)}
          </span>
          <h1 id="home-greeting" className={`k-display ${styles.greeting}`}>
            {greeting(me.user.display_name, new Date().getHours())}.{" "}
            {data && <span className="k-accent-text">{waitingText(versions, values)}</span>}
          </h1>
          <form className={styles.ask} onSubmit={ask} role="search">
            <Icon name="ask" className={styles.askIcon} />
            <label htmlFor="home-ask" className="visually-hidden">
              {DASHBOARD_TEXT.askLabel}
            </label>
            <input
              id="home-ask"
              className={styles.askInput}
              type="text"
              enterKeyHint="go"
              maxLength={1000}
              placeholder={DASHBOARD_TEXT.askPlaceholder}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
            />
            <button type="submit" className="k-btn k-btn--primary">
              {DASHBOARD_TEXT.ask}
              <Icon name="external" size="sm" />
            </button>
          </form>
          {suggestions.length > 0 && (
            <div className="k-chip-row">
              {suggestions.map((text) => (
                <Link key={text} className="k-suggest" href={askHref(text)}>
                  {text}
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className={styles.side}>
          {reviewer ? (
            <Link className={`k-blue-panel ${styles.panel}`} href="/review">
              <span className={styles.panelHead}>
                <span className={styles.pixels} aria-hidden="true" />
                <span className={styles.panelLabel}>{DASHBOARD_TEXT.reviewQueue}</span>
              </span>
              <span className={styles.panelBody}>
                <span className={styles.panelNumber}>{reviewTotal === null ? "…" : formatNumber(reviewTotal)}</span>
                <span className={styles.panelLine}>
                  {reviewTotal === 0 ? DASHBOARD_TEXT.reviewClear : DASHBOARD_TEXT.reviewLine}
                </span>
                <span className={styles.panelButton}>
                  {DASHBOARD_TEXT.openReview}
                  <Icon name="external" size="sm" />
                </span>
              </span>
            </Link>
          ) : (
            <Link className={`k-blue-panel ${styles.panel}`} href="/documents">
              <span className={styles.panelHead}>
                <span className={styles.pixels} aria-hidden="true" />
                <span className={styles.panelLabel}>{DASHBOARD_TEXT.libraryPanel}</span>
              </span>
              <span className={styles.panelBody}>
                <span className={styles.panelNumber}>{data ? formatNumber(data.tiles.documents) : "…"}</span>
                <span className={styles.panelLine}>{DASHBOARD_TEXT.libraryLine}</span>
                <span className={styles.panelButton}>
                  {DASHBOARD_TEXT.openDocuments}
                  <Icon name="external" size="sm" />
                </span>
              </span>
            </Link>
          )}
          <button type="button" className={`k-btn k-btn--lg ${styles.upload}`} onClick={() => inputRef.current?.click()}>
            <span className={styles.uploadWords}>
              <Icon name="upload" />
              {DASHBOARD_TEXT.uploadDocuments}
            </span>
            <span className={`k-faint ${styles.uploadWhere}`}>{chosenName ? `into ${chosenName}` : DASHBOARD_TEXT.uploadAutofile}</span>
          </button>
          <DepartmentPicker departments={departments} value={chosen} onChange={pick} />
        </div>
      </section>

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
        <div className="k-banner k-banner--danger" role="alert">
          <Icon name="alert" />
          <div className="k-banner-body">
            <span>{error}</span>
            <span>
              <button type="button" className="k-btn k-btn--sm" onClick={() => setAttempt((n) => n + 1)}>
                <Icon name="read-again" size="sm" />
                {DASHBOARD_TEXT.tryAgain}
              </button>
            </span>
          </div>
        </div>
      )}
      {data === null && error === null && (
        <p className="k-muted" role="status">
          {DASHBOARD_TEXT.loading}
        </p>
      )}
      {loaded !== null && <DashboardBody data={loaded.data} now={loaded.at} />}
    </div>
  );
}

/** Everything under the welcome, from the reply. Exported for the render test. */
export function DashboardBody({ data, now }: { data: DashboardData; now: number }) {
  const { tiles } = data;
  const shares = barShares(data.by_type.map((row) => row.count));
  const week = thisWeekText(tiles.documents_this_week);
  const departmentNames = data.departments.map((department) => department.name).join(", ");
  return (
    <>
      <section aria-label="Totals" className={`k-card ${styles.totals}`}>
        <Link href="/documents" className={styles.total}>
          <span className="k-eyebrow">{DASHBOARD_TEXT.tiles.documents}</span>
          <span className="k-num">{formatNumber(tiles.documents)}</span>
          {week && (
            <span className="k-stat-delta">
              <Icon name="send" size="sm" />
              {week}
            </span>
          )}
        </Link>
        <div className={styles.total}>
          <span className="k-eyebrow">{DASHBOARD_TEXT.tiles.departments}</span>
          <span className="k-num">{formatNumber(tiles.departments)}</span>
          {departmentNames && <span className="k-small k-muted">{departmentNames}</span>}
        </div>
        <div className={styles.total}>
          <span className="k-eyebrow">{DASHBOARD_TEXT.tiles.newVersions}</span>
          <span className="k-num">{formatNumber(tiles.new_versions_this_month)}</span>
        </div>
        {tiles.versions_to_confirm > 0 ? (
          <Link href="/documents?newer=1" className={styles.total}>
            <span className={`k-eyebrow ${styles.eyebrowRow}`}>
              {DASHBOARD_TEXT.tiles.toConfirm} <span className="k-ver k-ver--newer">{DASHBOARD_TEXT.waitingBadge}</span>
            </span>
            <span className={`k-num ${styles.waiting}`}>{formatNumber(tiles.versions_to_confirm)}</span>
            <span className={styles.more}>
              {DASHBOARD_TEXT.toConfirmLink}
              <Icon name="arrow-right" size="sm" />
            </span>
          </Link>
        ) : (
          <div className={styles.total}>
            <span className="k-eyebrow">{DASHBOARD_TEXT.tiles.toConfirm}</span>
            <span className="k-num">0</span>
          </div>
        )}
      </section>

      <div className={styles.columns}>
        <section className="k-card" aria-labelledby="by-type-heading">
          <div className="k-card-head">
            <div className={styles.headWords}>
              <span className="k-tagline">
                <b>02</b>
                <i />
                {DASHBOARD_TEXT.library}
              </span>
              <h2 id="by-type-heading" className="k-h2">
                {DASHBOARD_TEXT.byType}
              </h2>
            </div>
            <span className="k-spec">all departments · {formatNumber(tiles.documents)}</span>
          </div>
          {data.by_type.length === 0 ? (
            <p className="k-muted">{DASHBOARD_TEXT.byTypeEmpty}</p>
          ) : (
            <ul className={`k-bars ${styles.bars}`}>
              {data.by_type.map((row, index) => (
                <li key={row.doc_type ?? "unknown"} className="k-bar">
                  <span className="k-ellip">{docTypeLabel(row.doc_type)}</span>
                  <span className="k-bar-track" aria-hidden="true">
                    <span className={`k-bar-fill ${styles.fill}`} style={{ width: `${shares[index] ?? 0}%` }} />
                  </span>
                  <span className="k-bar-val">{formatNumber(row.count)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="k-card" aria-labelledby="departments-heading">
          <div className="k-card-head">
            <h2 id="departments-heading" className="k-h2">
              {DASHBOARD_TEXT.departments}
            </h2>
            <Link className="k-small" href="/documents">
              {DASHBOARD_TEXT.allFolders}
            </Link>
          </div>
          {data.departments.length === 0 && data.not_in_department === 0 ? (
            <p className="k-muted">{DASHBOARD_TEXT.departmentsEmpty}</p>
          ) : (
            <ul className={styles.departments}>
              {data.departments.map((department) => (
                <li key={department.id}>
                  <Link
                    className={styles.department}
                    href={libraryHref({ ...ALL_DOCUMENTS, folderId: department.id, subfolders: true })}
                  >
                    <span className={styles.departmentName}>
                      <Icon name="department" />
                      <strong>{department.name}</strong>
                    </span>
                    <span>
                      <span className={`k-mono ${styles.count}`}>{formatNumber(department.files)}</span>{" "}
                      <span className="k-small k-muted">{department.files === 1 ? "file" : "files"}</span>
                      <span className="visually-hidden"> ({filesText(department.files)})</span>
                    </span>
                  </Link>
                </li>
              ))}
              {data.not_in_department > 0 && (
                <li>
                  <Link className={`${styles.department} ${styles.outside}`} href="/documents">
                    <span className={styles.departmentName}>
                      <Icon name="folder" />
                      <strong>{DASHBOARD_TEXT.notInDepartment}</strong>
                    </span>
                    <span>
                      <span className={`k-mono ${styles.count}`}>{formatNumber(data.not_in_department)}</span>{" "}
                      <span className="k-small k-muted">{data.not_in_department === 1 ? "file" : "files"}</span>
                      <span className="visually-hidden"> ({filesText(data.not_in_department)})</span>
                    </span>
                    <span className={`k-small ${styles.sortThem}`}>{DASHBOARD_TEXT.sortThem}</span>
                  </Link>
                </li>
              )}
            </ul>
          )}
        </section>
      </div>

      <div className={styles.columns}>
        <section className="k-card k-card--flush" aria-labelledby="recent-heading">
          <div className="k-card-head k-card-head--bar">
            <div className={styles.headWords}>
              <span className="k-tagline">
                <b>03</b>
                <i />
                {DASHBOARD_TEXT.latest}
              </span>
              <h2 id="recent-heading" className="k-h2">
                {DASHBOARD_TEXT.recent}
              </h2>
            </div>
            <Link className="k-small" href="/documents">
              {DASHBOARD_TEXT.seeAll}
            </Link>
          </div>
          {data.recent.length === 0 ? (
            <p className={`k-muted ${styles.empty}`}>{DASHBOARD_TEXT.recentEmpty}</p>
          ) : (
            <ul className={styles.recent}>
              {data.recent.map((document) => (
                <li key={document.id} className="k-rule-row">
                  <span className={`k-name-cell ${styles.recentName}`}>
                    <span className="k-file-ic" data-ext={extensionOf(document.display_name)} aria-hidden="true" />
                    <span className={styles.recentWords}>
                      <Link className={`k-ellip ${styles.recentLink}`} href={`/documents/${document.id}`}>
                        {document.display_name}
                      </Link>
                      <span className="k-sub k-ellip">{docTypeLabel(document.doc_type)}</span>
                    </span>
                  </span>
                  <span className={styles.recentMeta}>
                    <LabelChip sensitivity={document.sensitivity} />
                    <span className="k-ver">v{document.version_no}</span>
                    <time className={`k-small k-faint ${styles.when}`} dateTime={document.created_at}>
                      {formatRelative(document.created_at, now)}
                    </time>
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="k-card" aria-labelledby="activity-heading">
          <div className="k-card-head">
            <h2 id="activity-heading" className="k-h2">
              {DASHBOARD_TEXT.activity}
            </h2>
          </div>
          {data.activity.length === 0 ? (
            <p className="k-muted">No activity yet.</p>
          ) : (
            <ol className="k-timeline" aria-label={DASHBOARD_TEXT.activity}>
              {data.activity.map((entry) => (
                <ActivityRow key={entry.id} entry={entry} />
              ))}
            </ol>
          )}
        </section>
      </div>
    </>
  );
}

/** The dot's icon and tone for an action: a version in the accent, a label change dashed. */
function activityMark(action: string): { icon: KIconName; tone: string } {
  if (action.startsWith("version.") || action === "document.version") return { icon: "versions", tone: "k-tl-dot--accent" };
  if (action === "document.label_change") return { icon: "tag", tone: "k-tl-dot--review" };
  const icons: Record<string, KIconName> = {
    "document.upload": "upload",
    "document.download": "download",
    "document.view": "eye",
    "document.move": "move",
    "document.autofile": "move",
    "document.trash": "trash",
    "document.restore": "undo",
    "document.reprocess": "read-again",
    "document.tags": "tag",
    "document.owner_change": "user",
  };
  return { icon: icons[action] ?? "clock", tone: "" };
}

function ActivityRow({ entry }: { entry: ActivityEntry }) {
  const document = activityDocument(entry);
  const linked = document !== null && entry.document && !entry.document.deleted ? entry.document.id : null;
  const mark = activityMark(entry.action);
  return (
    <li>
      <span className={`k-tl-dot ${mark.tone}`}>
        <Icon name={mark.icon} />
      </span>
      <span className={styles.activityWords}>
        <span>
          {activitySentence(entry)}
          {document !== null && (
            <>
              {" "}
              {linked ? <Link href={`/documents/${linked}`}>{document}</Link> : <span>{document}</span>}
            </>
          )}
        </span>
        <span className="k-tl-when">
          {entry.folder?.path ? `${entry.folder.path} · ` : ""}
          <time dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time>
        </span>
      </span>
    </li>
  );
}
