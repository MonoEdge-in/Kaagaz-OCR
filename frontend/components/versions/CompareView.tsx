"use client";

// Compare two versions (File management V11), and See the differences for a suggested pair
// (#521): the summary in plain words ("94% the same. 38 words changed on pages 2 and 5."), then
// two columns, page by page; removed words in red and struck through on the left, added words
// in green and underlined on the right, each also marked − or + and named for screen readers
// (never colour alone); a page with little text (a scan) shows its page images too, only where the
// page has a picture (PRD 9 F-08: a JSON, TXT or CSV file has none, so only its text).
// "Jump to next change" (the button, or N) goes to each change in turn. For a suggested pair
// the decision sits at the bottom, Upload as a new version or Upload as a new file
// (VersionChoice); after it the page goes to where it came from (?from=, as a document's back
// link), else the new upload's page, which shows the words once.
// The text is the reader's, shown as plain text.

import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { KeyboardEvent } from "react";
import { Icon } from "@/components/ui/Icon";
import { ApiError, compareDocuments, getDocument, pageImageUrl } from "@/lib/api";
import { FROM_PARAM, backFrom } from "@/lib/backLink";
import type { DocumentCompare, VersionSuggestion } from "@/lib/types";
import { compareColumns, nextChange } from "./compare";
import type { ColumnRun } from "./compare";
import { flashTarget, saveFlash } from "./flash";
import { VersionChoice } from "./VersionChoice";
import type { Chosen } from "./VersionChoice";
import { VERSION_TEXT } from "./words";
import styles from "./Versions.module.css";

// A page with fewer words than this on both sides is shown as its two images (kaagaz MIN_WORDS).
const FEW_WORDS = 20;

/** N jumps to the next change, only while the focus is inside the compare screen and nobody is
 * typing, with no Ctrl, Alt or Meta (WCAG 2.1.4: a one-key shortcut works only where it is). */
export function isJumpKey(
  event: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean },
  where: { inside: boolean; typing: boolean },
): boolean {
  if (!where.inside || where.typing || event.ctrlKey || event.metaKey || event.altKey) return false;
  return event.key.toLowerCase() === "n";
}

/** The pending suggestion this pair is, from the right side's detail; null otherwise. */
function pairSuggestion(suggestion: VersionSuggestion | null | undefined, withId: string): VersionSuggestion | null {
  if (!suggestion || suggestion.status !== "pending" || suggestion.role !== "new") return null;
  return suggestion.candidate.id === withId || suggestion.candidate.family_id === withId ? suggestion : null;
}

export function CompareView({ documentId, withId }: { documentId: string; withId: string }) {
  const router = useRouter();
  const from = backFrom(useSearchParams().get(FROM_PARAM));
  const [data, setData] = useState<DocumentCompare | null>(null);
  const [choice, setChoice] = useState<VersionSuggestion | null>(null);
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
    getDocument(documentId).then(
      (detail) => {
        if (!cancelled) setChoice(pairSuggestion(detail.version_suggestion, withId));
      },
      () => undefined, // no decision offered; the comparison still shows
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, withId, attempt]);

  function chosen(done: Chosen) {
    const target = flashTarget(from, documentId); // the words show on that document's page only
    const undoLinkOf = done.undo && choice ? choice.document.id : undefined;
    if (target !== null) saveFlash({ message: done.message, documentId: target, undoLinkOf });
    router.push(from?.href ?? `/documents/${encodeURIComponent(documentId)}`);
  }

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
        <p className="muted delayed" role="status">
          {VERSION_TEXT.loading}
        </p>
      )}
      {data !== null && <CompareBody data={data} choice={choice} onChosen={chosen} />}
    </div>
  );
}

/**
 * The heading, the summary, Jump to next change, the two columns and, for a suggested pair,
 * the decision. Exported for the render test.
 */
export function CompareBody({
  data,
  choice = null,
  onChosen,
}: {
  data: DocumentCompare;
  choice?: VersionSuggestion | null;
  onChosen?(chosen: Chosen): void;
}) {
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

  // N jumps too, while the focus is inside this screen (its keydown) and nobody is typing.
  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    const typing = event.target instanceof HTMLElement && event.target.closest("input, textarea, [contenteditable]") !== null;
    if (changes > 0 && isJumpKey(event, { inside: true, typing })) {
      event.preventDefault();
      goNext();
    }
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
              <span aria-hidden="true">−</span>
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
              <span aria-hidden="true">+</span>
              <span className="visually-hidden">{`${VERSION_TEXT.added}: `}</span>
              {run.text}
            </ins>
          ),
        )}
      </p>
    </div>
  );

  const before = choice ? VERSION_TEXT.yours(choice.candidate.name) : VERSION_TEXT.before;
  const after = choice ? VERSION_TEXT.upload(choice.document.name) : VERSION_TEXT.after;
  // A side's page image only when that file has the page (else it says so) and it has a picture (F-08).
  const image = (id: string, pageNo: number, pagesThere: number | null | undefined, pictured: boolean, whose: string) =>
    pagesThere !== null && pagesThere !== undefined && pageNo > pagesThere ? (
      <p className="muted">{VERSION_TEXT.noPage(pageNo)}</p>
    ) : pictured ? (
      <Image
        src={pageImageUrl(id, pageNo)}
        alt={`${VERSION_TEXT.page(pageNo)}, ${whose}`}
        width={600}
        height={800}
        unoptimized
      />
    ) : null;
  return (
    <div data-compare-area="true" onKeyDown={onKey}>
      <header className={styles.compareHead}>
        <div>
          <h1 className={styles.compareTitle}>{VERSION_TEXT.compareTitle}</h1>
          <p className="muted">{data.overview ?? (changes === 0 ? VERSION_TEXT.noChanges : data.summary)}</p>
        </div>
        {changes > 0 && (
          <button type="button" className="btn" onClick={goNext} aria-keyshortcuts="n">
            {VERSION_TEXT.nextChange}
            <Icon name="chevron-right" />
          </button>
        )}
      </header>
      <p className="visually-hidden" aria-live="polite">
        {said}
      </p>
      <div className={styles.legend} aria-hidden="true">
        <del className={styles.removed}>
          <span aria-hidden="true">−</span>
          {VERSION_TEXT.removed}
        </del>
        <ins className={styles.added}>
          <span aria-hidden="true">+</span>
          {VERSION_TEXT.added}
        </ins>
      </div>
      {pages.map((page, index) => {
        const sent = data.pages[index];
        const [left, right] = [data.image_pages.left.includes(page.page_no), data.image_pages.right.includes(page.page_no)];
        const scan =
          (left || right) &&
          sent?.left_words !== undefined &&
          sent.right_words !== undefined &&
          sent.left_words < FEW_WORDS &&
          sent.right_words < FEW_WORDS;
        return (
          <section key={page.page_no} className={styles.comparePage} aria-label={VERSION_TEXT.page(page.page_no)}>
            <h2 className={styles.pageHeading}>{VERSION_TEXT.page(page.page_no)}</h2>
            {sent?.truncated && <p className="muted">{VERSION_TEXT.truncated}</p>}
            {scan && (
              <div className={styles.pageImages}>
                {image(data.left_id, page.page_no, data.left_pages, left, before)}
                {image(data.right_id, page.page_no, data.right_pages, right, after)}
              </div>
            )}
            <div className={styles.columns}>
              {/* `with` is the left side (the version compared with), this document the right. */}
              {side(before, page.left)}
              {side(after, page.right)}
            </div>
          </section>
        );
      })}
      {choice && onChosen && (
        <div className={styles.banner}>
          <VersionChoice suggestion={choice} showCompare={false} onDone={onChosen} />
        </div>
      )}
    </div>
  );
}
