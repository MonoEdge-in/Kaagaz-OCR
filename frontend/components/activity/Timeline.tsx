"use client";

// A timeline (File management A5): the rows newest first, each "Priya viewed" with the
// document (a deleted one says so), where it is and when; "Show more" asks for the next
// page by its cursor. Used by the document page's Activity tab and the dashboard, and (PRD 7,
// #601) Management's Access activity page, where an entry someone gave themselves has its chip.
// A row about no document says no folder (an access change's place is already in its words).
// Every name is React text, never markup.

import Link from "next/link";
import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { ActivityEntry, ActivityPage } from "@/lib/types";
import { ACTIVITY_TEXT, SELF_GIVEN, activityDocument, activitySentence, selfGiven } from "./words";
import styles from "./Activity.module.css";

export function Timeline({
  load,
  showDocument = false,
  label,
  empty = ACTIVITY_TEXT.empty,
}: {
  load: (cursor?: string) => Promise<ActivityPage>;
  showDocument?: boolean;
  label: string; // the list's name for screen readers
  empty?: string; // what an empty list says
}) {
  const [items, setItems] = useState<ActivityEntry[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    load().then(
      (page) => {
        if (cancelled) return;
        setItems(page.items);
        setNext(page.next_cursor ?? null);
        setError(null);
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught instanceof ApiError ? caught.message : ACTIVITY_TEXT.loadFailed);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [load, attempt]);

  function more() {
    if (next === null) return;
    setBusy(true);
    load(next).then(
      (page) => {
        setItems((shown) => [...(shown ?? []), ...page.items]);
        setNext(page.next_cursor ?? null);
        setBusy(false);
      },
      (caught: unknown) => {
        setError(caught instanceof ApiError ? caught.message : ACTIVITY_TEXT.loadFailed);
        setBusy(false);
      },
    );
  }

  if (error !== null && items === null) {
    return (
      <div className={styles.note} role="alert">
        <p>
          {ACTIVITY_TEXT.loadFailed} {error === ACTIVITY_TEXT.loadFailed ? null : error}
        </p>
        <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
          <Icon name="refresh" />
          {ACTIVITY_TEXT.tryAgain}
        </button>
      </div>
    );
  }
  if (items === null) {
    return (
      <p className="muted delayed" role="status">
        {ACTIVITY_TEXT.loading}
      </p>
    );
  }
  if (items.length === 0) return <p className="muted">{empty}</p>;
  return (
    <>
      <ol className={styles.timeline} aria-label={label}>
        {items.map((entry) => (
          <TimelineRow key={entry.id} entry={entry} showDocument={showDocument} />
        ))}
      </ol>
      {error !== null && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {next !== null && (
        <button type="button" className="btn btn-quiet" onClick={more} disabled={busy}>
          {ACTIVITY_TEXT.more}
        </button>
      )}
    </>
  );
}

export function TimelineRow({ entry, showDocument }: { entry: ActivityEntry; showDocument: boolean }) {
  const document = showDocument ? activityDocument(entry) : null;
  const linked = document !== null && entry.document && !entry.document.deleted ? entry.document.id : null;
  return (
    <li className={styles.row}>
      <span className={styles.what}>{activitySentence(entry)}</span>
      {selfGiven(entry) && (
        <span className="status-chip" data-tone="neutral">
          {SELF_GIVEN}
        </span>
      )}
      {document !== null &&
        (linked ? (
          <Link href={`/documents/${linked}`} className={styles.document}>
            {document}
          </Link>
        ) : (
          <span className={styles.document}>{document}</span>
        ))}
      <span className={styles.meta}>
        {entry.document && entry.folder?.path ? `${entry.folder.path} · ` : ""}
        <time dateTime={entry.created_at}>{formatDateTime(entry.created_at)}</time>
      </span>
    </li>
  );
}
