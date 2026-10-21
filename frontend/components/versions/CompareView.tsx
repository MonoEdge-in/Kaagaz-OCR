"use client";

// Compare two versions (File management V11): two columns, page by page; removed words in
// red and struck through on the left, added words in green and underlined on the right (the
// marks say it as well as the colours); "Next change" goes to each change in turn. The
// text is the reader's, shown as plain text.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError, compareDocuments } from "@/lib/api";
import type { DocumentCompare } from "@/lib/types";
import { compareColumns, nextChange } from "./compare";
import type { ColumnRun } from "./compare";
import { VERSION_TEXT } from "./words";
import styles from "./Versions.module.css";

export function CompareView({ documentId, withId }: { documentId: string; withId: string }) {
  const [data, setData] = useState<DocumentCompare | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    compareDocuments(documentId, withId).then(
      (reply) => {
        if (!cancelled) {
          setData(reply);
          setError(null);
        }
      },
      (caught: unknown) => {
        if (!cancelled) setError(caught instanceof ApiError ? caught.message : VERSION_TEXT.loadFailed);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, withId, attempt]);

  return (
    <div className="page page-wide">
      <Link href={`/documents/${encodeURIComponent(documentId)}`} className={styles.back}>
        <Icon name="chevron-left" />
        {VERSION_TEXT.back}
      </Link>
      {error !== null && data === null && (
        <div className={styles.banner} role="alert">
          <p className={styles.bannerText}>{error}</p>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {data === null && error === null && (
        <p className="muted" role="status">
          {VERSION_TEXT.loading}
        </p>
      )}
      {data !== null && <CompareBody data={data} />}
    </div>
  );
}

/** The heading, the summary, Next change and the two columns. Exported for the render test. */
export function CompareBody({ data }: { data: DocumentCompare }) {
  const { pages, changes } = useMemo(() => compareColumns(data.pages), [data.pages]);
  const [at, setAt] = useState(-1);
  const [said, setSaid] = useState("");

  function goNext() {
    const next = nextChange(at, changes);
    setAt(next);
    const target = document.querySelector<HTMLElement>(`[data-change="${next}"]`);
    target?.scrollIntoView({ block: "center" });
    target?.focus({ preventScroll: true });
    setSaid(`Change ${next + 1} of ${changes}.`);
  }

  // Both columns carry the change's number; the left comes first in the page, so "Next change"
  // lands on what was removed, or on what was added when nothing was.
  const side = (title: string, runs: ColumnRun[]) => (
    <div className={styles.column}>
      <p className={styles.columnTitle}>{title}</p>
      <p className={`readText ${styles.text}`}>
        {runs.map((run, index) =>
          run.kind === "same" ? (
            <span key={index}>{run.text}</span>
          ) : run.kind === "removed" ? (
            <del
              key={index}
              className={styles.removed}
              data-change={run.change ?? undefined}
              data-current={run.change === at ? "true" : undefined}
              tabIndex={-1}
            >
              <span className="visually-hidden">{`${VERSION_TEXT.removed}: `}</span>
              {run.text}
            </del>
          ) : (
            <ins
              key={index}
              className={styles.added}
              data-change={run.change ?? undefined}
              data-current={run.change === at ? "true" : undefined}
              tabIndex={-1}
            >
              <span className="visually-hidden">{`${VERSION_TEXT.added}: `}</span>
              {run.text}
            </ins>
          ),
        )}
      </p>
    </div>
  );

  return (
    <>
      <header className={styles.compareHead}>
        <div>
          <h1 className={styles.compareTitle}>{VERSION_TEXT.compareTitle}</h1>
          <p className="muted">{changes === 0 ? VERSION_TEXT.noChanges : data.summary}</p>
        </div>
        {changes > 0 && (
          <button type="button" className="btn" onClick={goNext}>
            {VERSION_TEXT.nextChange}
            <Icon name="chevron-right" />
          </button>
        )}
      </header>
      <p className="visually-hidden" aria-live="polite">
        {said}
      </p>
      <div className={styles.legend} aria-hidden="true">
        <del className={styles.removed}>{VERSION_TEXT.removed}</del>
        <ins className={styles.added}>{VERSION_TEXT.added}</ins>
      </div>
      {pages.map((page, index) => (
        <section key={page.page_no} className={styles.comparePage} aria-label={VERSION_TEXT.page(page.page_no)}>
          <h2 className={styles.pageHeading}>{VERSION_TEXT.page(page.page_no)}</h2>
          {data.pages[index]?.truncated && <p className="muted">{VERSION_TEXT.truncated}</p>}
          <div className={styles.columns}>
            {/* `with` is the left side (the version compared with), this document the right. */}
            {side(VERSION_TEXT.before, page.left)}
            {side(VERSION_TEXT.after, page.right)}
          </div>
        </section>
      ))}
    </>
  );
}
