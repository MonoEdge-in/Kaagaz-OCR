"use client";

// The review queue: every value labelled needs review, oldest first. Each one
// leaves the list once it is resolved, and the page says so politely. Members
// see a short note instead; the server refuses them anyway.

import { useEffect, useState } from "react";
import { ApiError, listReview } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ReviewItem, ReviewList } from "@/lib/types";
import { appendItems, canReview, resolvedMessage, withoutItem } from "./review";
import { ReviewCard } from "./ReviewCard";

const PAGE_SIZE = 20;

const messageOf = (error: unknown) =>
  error instanceof ApiError ? error.message : "Could not load the review queue.";

function Queue() {
  const [list, setList] = useState<ReviewList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  useEffect(() => {
    let cancelled = false;
    listReview({ limit: PAGE_SIZE })
      .then((first) => {
        if (!cancelled) setList(first);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(messageOf(caught));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadMore = async () => {
    if (!list) return;
    setLoadingMore(true);
    setError(null);
    try {
      // Resolved values have left the queue, so the next page starts after what is shown.
      const more = await listReview({ limit: PAGE_SIZE, offset: list.items.length });
      setList((current) => (current ? appendItems(current, more) : more));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setLoadingMore(false);
    }
  };

  const onResolved = (before: ReviewItem, after: ReviewItem) => {
    setList((current) => (current ? withoutItem(current, before.value.id) : current));
    setAnnouncement(resolvedMessage(after));
  };

  return (
    <div className="stack">
      <h1>Review</h1>
      <p className="muted">
        These values were not fully checked when they were read. Pick the right reading, or type
        it.
      </p>
      <p aria-live="polite">{announcement}</p>
      {error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : null}
      {list === null && !error ? <p className="muted">Loading the queue…</p> : null}
      {list !== null ? (
        <>
          <p aria-live="polite">
            {list.total === 0
              ? "Nothing to review. Every value is settled."
              : `${list.total} value${list.total === 1 ? "" : "s"} to review.`}
          </p>
          {list.items.map((item) => (
            <ReviewCard key={item.value.id} item={item} onResolved={onResolved} />
          ))}
          {list.items.length < list.total ? (
            <div>
              <button type="button" className="btn" disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? "Loading…" : "Show more"}
              </button>
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export function ReviewQueue() {
  const { me } = useSession();
  if (!canReview(me.user.role)) {
    return (
      <div className="stack">
        <h1>Review</h1>
        <p>Only reviewers and admins can correct values. Ask your admin if you need to.</p>
      </div>
    );
  }
  return <Queue />;
}
