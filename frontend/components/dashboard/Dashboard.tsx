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
import type { ChangeEvent, CSSProperties, FormEvent, ReactNode } from "react";
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
import { ApiError, getDashboard, getReadyQuestions, listFolders, listReview } from "@/lib/api";
import { docTypeLabel, formatDateTime, formatNumber, formatRelative } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { ActivityEntry, Dashboard as DashboardData, Folder, ReadyQuestion } from "@/lib/types";
import {
  DASHBOARD_TEXT,
  barShares,
  browseTitle,
  checkTitle,
  extensionOf,
  filesText,
  greeting,
  thisWeekText,
  todayLine,
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
  const [questions, setQuestions] = useState<ReadyQuestion[] | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { uploads, setTrayOpen } = useSharedUploads(() => setAttempt((n) => n + 1));
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

  // Home's questions; their answers are made just after the first reply, so Home looks again
  // at READY_LOOKS until every one is ready, and offers each only once it is.
  useEffect(() => {
    let cancelled = false;
    let again: number | undefined;
    const load = (look: number) =>
      getReadyQuestions().then(
        (reply) => {
          if (cancelled) return;
          setQuestions(reply.items);
          const wait = READY_LOOKS[look];
          if (wait !== undefined && reply.items.some((item) => !item.ready)) again = window.setTimeout(() => void load(look + 1), wait);
        },
        () => {
          if (!cancelled) setQuestions([]); // the card says what to do instead
        },
      );
    void load(0);
    return () => {
      cancelled = true;
      window.clearTimeout(again);
    };
  }, []);

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

  return (
    <div className={`page ${styles.page}`}>
      <section className={`k-dotfield ${styles.welcome}`} aria-labelledby="home-greeting">
        <p className={styles.today}>{todayLine(new Date(), me.customer.name)}</p>
        <h1 id="home-greeting" className={`k-display ${styles.greeting}`}>
          {greeting(me.user.display_name, new Date().getHours())}
        </h1>
        <p className={styles.lead}>{DASHBOARD_TEXT.lead}</p>
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
          <button type="submit" className={`k-btn k-btn--primary ${styles.askButton}`}>
            {DASHBOARD_TEXT.ask}
            <Icon name="external" size="sm" />
          </button>
        </form>
        <ReadyQuestions questions={questions} />
      </section>

      <HomeActions
        reviewer={reviewer}
        reviewTotal={reviewTotal}
        documents={data ? data.tiles.documents : null}
        picker={<DepartmentPicker departments={departments} value={chosen} onChange={pick} />}
        onUpload={() => inputRef.current?.click()}
      />

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

/** How long Home waits before each further look for ready answers (the first comes at once). */
export const READY_LOOKS = [12_000, 40_000];

/** Under Ask (Tushar, 24 Oct 2026): this person's usual questions, else ones that suit their
 * library, each offered only once its answer is ready, so a press answers in under a second;
 * nothing while none is. Exported for the render test. */
export function ReadyQuestions({ questions }: { questions: ReadyQuestion[] | null }) {
  const ready = (questions ?? []).filter((item) => item.ready);
  if (ready.length === 0) return null;
  const yours = ready.some((item) => item.yours);
  return (
    <div className={styles.readyRow}>
      <span className={styles.readyLabel}>{yours ? DASHBOARD_TEXT.yourQuestions : DASHBOARD_TEXT.questionsToTry}</span>
      <ul className={styles.readyList}>
        {ready.map((item, index) => (
          <li key={item.text} style={{ "--i": index } as CSSProperties}>
            <Link className={`k-suggest ${styles.readyQuestion}`} href={askHref(item.text)}>
              <Icon name="check-circle" size="sm" />
              {item.text}
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** One card per next step (24 Oct 2026): Check values (a reviewer's), Upload documents with
 * where they go, and Browse documents; each a plain sentence and a button that says where it
 * goes. Exported for the render test. */
export function HomeActions({
  reviewer,
  reviewTotal,
  documents,
  picker,
  onUpload,
}: {
  reviewer: boolean;
  reviewTotal: number | null;
  documents: number | null;
  picker: ReactNode;
  onUpload: () => void;
}) {
  const waiting = reviewTotal !== null && reviewTotal > 0;
  let index = 0;
  const order = () => ({ "--i": index++ }) as CSSProperties;
  return (
    <section className={styles.actions} aria-labelledby="home-steps">
      <h2 id="home-steps" className="visually-hidden">
        {DASHBOARD_TEXT.steps}
      </h2>
      {reviewer && (
        <Link className={`${styles.action} ${waiting ? styles.actionWaiting : ""}`} href="/review" style={order()}>
          <span className={styles.actionIcon} aria-hidden="true">
            <Icon name="review" />
          </span>
          <span className={styles.actionTitle}>{checkTitle(reviewTotal)}</span>
          <span className={styles.actionText}>{waiting || reviewTotal === null ? DASHBOARD_TEXT.checkLine : DASHBOARD_TEXT.checkClear}</span>
          <span className={styles.actionGo}>
            {DASHBOARD_TEXT.openReview}
            <Icon name="arrow-right" size="sm" />
          </span>
        </Link>
      )}
      <div className={styles.action} style={order()}>
        <span className={styles.actionIcon} aria-hidden="true">
          <Icon name="upload" />
        </span>
        <span className={styles.actionTitle}>{DASHBOARD_TEXT.uploadDocuments}</span>
        <span className={styles.actionText}>{DASHBOARD_TEXT.uploadLine}</span>
        <div className={styles.uploadTo}>{picker}</div>
        <button type="button" className={`k-btn k-btn--primary ${styles.choose}`} onClick={onUpload}>
          <Icon name="upload" size="sm" />
          {DASHBOARD_TEXT.chooseFiles}
        </button>
      </div>
      <Link className={styles.action} href="/documents" style={order()}>
        <span className={styles.actionIcon} aria-hidden="true">
          <Icon name="folder" />
        </span>
        <span className={styles.actionTitle}>{browseTitle(documents)}</span>
        <span className={styles.actionText}>{DASHBOARD_TEXT.browseLine}</span>
        <span className={styles.actionGo}>
          {DASHBOARD_TEXT.openDocuments}
          <Icon name="arrow-right" size="sm" />
        </span>
      </Link>
    </section>
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
            <ul className={`${styles.recent} ${styles.scroll}`}>
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
            <ol className={`k-timeline ${styles.scroll}`} aria-label={DASHBOARD_TEXT.activity}>
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
