"use client";

// The review queue: every value labelled needs review, oldest first, or only
// those of one reason (PRD 3 Y5). Each one leaves the list once it is
// resolved, the page says so politely in one message, and focus moves to the
// next card so it is never lost. Members see a short note instead; the server
// refuses them anyway. "By document" (PRD 5 U6) puts the cards shown under
// their document's name. UI audit (16 Oct 2026): a segmented control picks the view
// (R6); "One at a time" (R3, the default) shows one card with "3 of 15", takes the
// keys 1, 2, E, S and Enter, and moves on by itself after a save or a skip.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { ApiError, FEATURE_OFF, listReview } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ReviewItem, ReviewList, ReviewReason } from "@/lib/types";
import type { QueueView } from "./review";
import {
  REASON_FILTERS,
  QUEUE_VIEWS,
  appendItems,
  canReview,
  focusAfterResolve,
  focusIndexAfter,
  focusProgress,
  groupByDocument,
  leftToReview,
  parseReason,
  queueParams,
  resolvedMessage,
  withoutItem,
} from "./review";
import { ReviewCard, reviewTitleId } from "./ReviewCard";
import styles from "./Review.module.css";

const PAGE_SIZE = 20;
const SKELETON_CARDS = [0, 1];
const EMPTY_HEADING_ID = "review-empty-title";

const messageOf = (error: unknown) =>
  error instanceof ApiError ? error.message : "Check your internet connection, then try again.";

function Header() {
  return (
    <header className="page-header">
      <div>
        <h1>Review</h1>
        <p>Values the readers were not sure about. Pick the right reading, or type it.</p>
      </div>
    </header>
  );
}

function Queue({ documentId }: { documentId?: string }) {
  const [attempt, setAttempt] = useState(0);
  const [first, setFirst] = useState<{ attempt: number; list: ReviewList } | null>(null);
  const [loadError, setLoadError] = useState<{ attempt: number; message: string } | null>(null);
  const [list, setList] = useState<ReviewList | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [saved, setSaved] = useState("");
  const [reason, setReason] = useState<ReviewReason | null>(null);
  const [view, setView] = useState<QueueView>("focus");
  // Focus mode: the card shown (its place in the list) and how many were settled in this visit.
  const [index, setIndex] = useState(0);
  const [done, setDone] = useState(0);
  // The latest list, kept in step with every setList, so a resolve that lands
  // after another one works on the current queue.
  const latest = useRef<ReviewList | null>(null);
  // The element id to focus once the list has re-rendered.
  const focusNext = useRef<string | null>(null);

  const commit = (next: ReviewList) => {
    latest.current = next;
    setList(next);
  };

  useEffect(() => {
    let cancelled = false;
    listReview(queueParams(0, PAGE_SIZE, documentId, reason))
      .then((result) => {
        if (cancelled) return;
        setFirst({ attempt, list: result });
        latest.current = result;
        setList(result);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setLoadError({ attempt, message: messageOf(caught) });
      });
    return () => {
      cancelled = true;
    };
  }, [attempt, documentId, reason]);

  useEffect(() => {
    const id = focusNext.current;
    if (id === null) return;
    focusNext.current = null;
    document.getElementById(id)?.focus();
  }, [list]);

  const loaded = first !== null && first.attempt === attempt ? list : null;
  const failed = loadError !== null && loadError.attempt === attempt ? loadError.message : null;
  const announcement = saved && loaded ? `${saved} ${leftToReview(loaded.total)}` : "";
  // Focus mode's one card: the one at `index`, or the last when a load to fill it failed.
  const at = loaded ? Math.min(index, loaded.items.length - 1) : 0;
  const shown = loaded?.items[at];

  /** Loads the next page; with focusAt, focus then goes to the card there (or the first, or the empty heading). */
  const loadMore = async (focusAt: number | null = null) => {
    const shown = latest.current;
    if (!shown) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      // Resolved values have left the queue, so the next page starts after what is shown.
      const more = await listReview(queueParams(shown.items.length, PAGE_SIZE, documentId, reason));
      const next = latest.current ? appendItems(latest.current, more) : more;
      if (focusAt !== null) {
        const top = next.items[focusAt] ?? next.items[0];
        if (!next.items[focusAt]) setIndex(0);
        focusNext.current = top ? reviewTitleId(top.value.id) : EMPTY_HEADING_ID;
      }
      commit(next);
    } catch (caught) {
      setMoreError(messageOf(caught));
    } finally {
      setLoadingMore(false);
    }
  };

  /** Focus mode: show the card at `at` of `list`, loading more there when the server has them. */
  const showAt = (list: ReviewList, at: number) => {
    const place = focusIndexAfter(list, at);
    if (place === "more") {
      setIndex(at);
      void loadMore(at);
    } else {
      const item = place === null ? undefined : list.items[place];
      setIndex(place ?? 0);
      focusNext.current = item ? reviewTitleId(item.value.id) : EMPTY_HEADING_ID;
    }
  };

  const onResolved = (before: ReviewItem, after: ReviewItem) => {
    const current = latest.current;
    if (!current) return;
    const next = withoutItem(current, before.value.id);
    // Decide where focus goes before the card leaves.
    const target = focusAfterResolve(current, before.value.id);
    commit(next);
    setSaved(resolvedMessage(after));
    if (view === "focus") {
      setDone((n) => n + 1);
      showAt(next, current.items.findIndex((item) => item.value.id === before.value.id));
    } else if (target.kind === "card") focusNext.current = reviewTitleId(target.valueId);
    else if (target.kind === "empty") focusNext.current = EMPTY_HEADING_ID;
    else void loadMore(0);
  };

  const skip = () => {
    const current = latest.current;
    if (!current) return;
    // The only value left comes back: the line says why nothing moved.
    setSaved(current.total === 1 ? "This is the only value left." : "");
    showAt(current, index + 1);
  };

  // Review switched off for the whole product: a calm note in its place.
  if (failed === FEATURE_OFF) {
    return (
      <div className="page">
        <Header />
        <EmptyState icon="review" title={FEATURE_OFF} />
      </div>
    );
  }

  return (
    <div className="page">
      <Header />
      <div className="field">
        <label htmlFor="review-reason">Show values where</label>
        <select
          id="review-reason"
          className="input"
          value={reason ?? ""}
          // A "Show more" still loading belongs to the old filter: wait for it.
          disabled={loadingMore}
          onChange={(event) => {
            setMoreError(null);
            setReason(parseReason(event.target.value));
            setIndex(0);
            setDone(0);
            setAttempt((n) => n + 1); // the loading state until the filtered list comes
          }}
        >
          <option value="">Any reason</option>
          {REASON_FILTERS.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.text}
            </option>
          ))}
        </select>
      </div>
      {/* Native radios: arrow keys move between them, and Tab stops once (R6). */}
      <div className={styles.segments} role="radiogroup" aria-label="Show">
        {QUEUE_VIEWS.map((choice) => (
          <label key={choice.value} className={styles.segment}>
            <input
              type="radio"
              name="review-view"
              value={choice.value}
              checked={view === choice.value}
              onChange={() => setView(choice.value)}
            />
            {choice.text}
          </label>
        ))}
      </div>
      <p className={announcement ? styles.saved : "visually-hidden"} aria-live="polite">
        {announcement ? <Icon name="check-seal" size={16} /> : null}
        {announcement}
      </p>

      {failed && !loaded ? (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>Could not load the review queue.</p>
            <p>{failed}</p>
          </div>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      ) : null}

      {!loaded && !failed ? (
        <>
          <p className="visually-hidden" role="status">
            Loading the review queue…
          </p>
          <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
            {SKELETON_CARDS.map((card) => (
              <span key={card} className={`skeleton ${styles.skeletonCard}`} />
            ))}
          </div>
        </>
      ) : null}

      {loaded ? (
        loaded.total === 0 ? (
          <EmptyState
            icon="check-seal"
            title={reason ? "Nothing to review for this reason" : "Nothing to review"}
            headingId={EMPTY_HEADING_ID}
            action={
              <Link href="/documents" className="btn">
                Go to Documents
              </Link>
            }
          >
            {reason
              ? "Choose Any reason to see the rest of the queue."
              : "Every value read so far is verified or already settled."}
          </EmptyState>
        ) : (
          <>
            <p className={`${styles.count} tabular`}>
              {view === "focus" && shown
                ? `Value ${focusProgress(done, at, loaded.total)}`
                : `${loaded.total} value${loaded.total === 1 ? "" : "s"} to review.`}
            </p>
            {view === "focus" ? (
              shown ? (
                <ReviewCard key={shown.value.id} item={shown} onResolved={onResolved} onSkip={skip} />
              ) : (
                <p className="muted">Loading the next value…</p>
              )
            ) : view === "document" ? (
              groupByDocument(loaded.items).map((group) => (
                <section key={group.id} className={styles.group} aria-labelledby={`review-group-${group.id}`}>
                  <h2 id={`review-group-${group.id}`} className={styles.groupHeading}>
                    {group.name}
                    <span className="muted">{` · ${group.items.length} shown`}</span>
                  </h2>
                  <div className={styles.cards}>
                    {group.items.map((item) => (
                      <ReviewCard key={item.value.id} item={item} onResolved={onResolved} />
                    ))}
                  </div>
                </section>
              ))
            ) : (
              <div className={styles.cards}>
                {loaded.items.map((item) => (
                  <ReviewCard key={item.value.id} item={item} onResolved={onResolved} />
                ))}
              </div>
            )}
            {moreError ? (
              <p className="error-text" role="alert">
                {`Could not load more values. ${moreError}`}
              </p>
            ) : null}
            {view !== "focus" && loaded.items.length < loaded.total ? (
              <div>
                <button
                  type="button"
                  className={`btn ${loadingMore ? styles.busy : ""}`}
                  disabled={loadingMore}
                  aria-busy={loadingMore || undefined}
                  onClick={() => void loadMore()}
                >
                  {loadingMore && <span className="spinner" aria-hidden="true" />}
                  Show more
                </button>
              </div>
            ) : null}
          </>
        )
      ) : null}
    </div>
  );
}

export function ReviewQueue() {
  const { me } = useSession();
  const searchParams = useSearchParams();
  const documentId = searchParams.get("document_id") || undefined;
  if (!canReview(me.user.role)) {
    return (
      <div className="page">
        <Header />
        <EmptyState icon="review" title="Review is for reviewers and admins">
          Only reviewers and admins can correct values. Ask your admin if you need to.
        </EmptyState>
      </div>
    );
  }
  return <Queue documentId={documentId} />;
}
