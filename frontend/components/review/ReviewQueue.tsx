"use client";

// The review queue: every value labelled needs review, oldest first. Each one
// leaves the list once it is resolved, the page says so politely in one
// message, and focus moves to the next card so it is never lost. Members see a
// short note instead; the server refuses them anyway.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { ApiError, listReview } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ReviewItem, ReviewList } from "@/lib/types";
import {
  appendItems,
  canReview,
  focusAfterResolve,
  leftToReview,
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

  /** Loads the next page; with focusFirst, focus then goes to its first card (or the empty heading). */
  const loadMore = async (focusFirst = false) => {
    const shown = latest.current;
    if (!shown) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      // Resolved values have left the queue, so the next page starts after what is shown.
      const more = await listReview({
        limit: PAGE_SIZE,
        offset: shown.items.length,
        document_id: documentId,
      });
      const next = latest.current ? appendItems(latest.current, more) : more;
      if (focusFirst) {
        const top = next.items[0];
        focusNext.current = top ? reviewTitleId(top.value.id) : EMPTY_HEADING_ID;
      }
      commit(next);
    } catch (caught) {
      setMoreError(messageOf(caught));
    } finally {
      setLoadingMore(false);
    }
  };

  const onResolved = (before: ReviewItem, after: ReviewItem) => {
    const current = latest.current;
    if (!current) return;
    // Decide where focus goes before the card leaves.
    const target = focusAfterResolve(current, before.value.id);
    commit(withoutItem(current, before.value.id));
    setSaved(resolvedMessage(after));
    if (target.kind === "card") focusNext.current = reviewTitleId(target.valueId);
    else if (target.kind === "empty") focusNext.current = EMPTY_HEADING_ID;
    else void loadMore(true);
  };

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
            title="Nothing to review"
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
              {`${loaded.total} value${loaded.total === 1 ? "" : "s"} to review.`}
            </p>
            <div className={styles.cards}>
              {loaded.items.map((item) => (
                <ReviewCard key={item.value.id} item={item} onResolved={onResolved} />
              ))}
            </div>
            {moreError ? (
              <p className="error-text" role="alert">
                {`Could not load more values. ${moreError}`}
              </p>
            ) : null}
            {loaded.items.length < loaded.total ? (
              <div>
                <button
                  type="button"
                  className="btn"
                  disabled={loadingMore}
                  onClick={() => void loadMore()}
                >
                  {loadingMore ? (
                    <>
                      <span className="spinner" aria-hidden="true" />
                      Loading…
                    </>
                  ) : (
                    "Show more"
                  )}
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
