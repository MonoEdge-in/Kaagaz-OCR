"use client";

// The review queue: every value labelled needs review, oldest first, or only
// those of one reason (PRD 3 Y5). Each one leaves the list once it is
// resolved, the page says so politely in one message, and focus moves to the
// next card so it is never lost. Members see a short note instead; the server
// refuses them anyway. "By document" (PRD 5 U6) puts the cards shown under
// their document's name. UI audit (16 Oct 2026): a segmented control picks the view
// (R6); "One at a time" (R3, the default) shows one card with "3 of 15", takes the
// keys 1, 2, E, S and Enter, and moves on by itself after a save or a skip.
// Verify all (beside "Value N of M") asks once, inline, then verifies every value the
// filters show in one call; the queue is locked until it ends, then loads again.
// PRD 9 F-17: Review opens on "By document" (ReviewDocuments.tsx), the documents with open values
// or open word flags, unless the address asks for one document's or one reason's values; the
// reason chips are one Filter menu (RowMenu), for the value views.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { RowMenu } from "@/components/admin/RowMenu";
import { StillLoading } from "@/components/shell/StillLoading";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { ApiError, FEATURE_OFF, listReview, verifyAllReview } from "@/lib/api";
import { BackFrom } from "@/lib/backLink";
import { useSession } from "@/lib/session";
import type { ReviewItem, ReviewList, ReviewReason } from "@/lib/types";
import type { QueueView } from "./review";
import {
  REASON_FILTERS,
  QUEUE_VIEWS,
  appendItems,
  canReview,
  canVerifyAll,
  focusAfterResolve,
  focusIndexAfter,
  focusProgress,
  leftToReview,
  parseReason,
  queueParams,
  reasonText,
  resolvedMessage,
  reviewHref,
  verifyAllFilters,
  verifyAllQuestion,
  verifyAllText,
  withoutItem,
} from "./review";
import { ReviewCard, reviewTitleId } from "./ReviewCard";
import { ReviewDocuments } from "./ReviewDocuments";
import styles from "./Review.module.css";

const PAGE_SIZE = 20;
const SKELETON_CARDS = [0, 1];
const EMPTY_HEADING_ID = "review-empty-title";
const VERIFY_ALL_RESULT_ID = "review-verify-all-result";

const messageOf = (error: unknown) =>
  error instanceof ApiError ? error.message : "Check your internet connection, then try again.";

/** The way back to the document a "Review values" link came from (Tushar, 28 Oct 2026). */
type Back = { href: string; name: string };

function Header({ back }: { back?: Back }) {
  return (
    <header className="page-header">
      <div>
        {back ? (
          <Link href={back.href} className={styles.back}>
            <Icon name="chevron-left" />
            <span>{`Back to ${back.name}`}</span>
          </Link>
        ) : null}
        <h1>Review</h1>
        <p>Values the readers were not sure about. Pick the right reading, or type it.</p>
      </div>
    </header>
  );
}

function Queue({ documentId, initialReason }: { documentId?: string; initialReason: ReviewReason | null }) {
  const { me } = useSession();
  const [attempt, setAttempt] = useState(0);
  const [first, setFirst] = useState<{ attempt: number; list: ReviewList } | null>(null);
  const [loadError, setLoadError] = useState<{ attempt: number; message: string } | null>(null);
  const [list, setList] = useState<ReviewList | null>(null);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [saved, setSaved] = useState("");
  const [reason, setReason] = useState<ReviewReason | null>(initialReason);
  const [view, setView] = useState<QueueView>(documentId || initialReason ? "focus" : "document");
  const values = view !== "document"; // the value views: One at a time and All items
  // Focus mode: the card shown (its place in the list) and how many were settled in this visit.
  const [index, setIndex] = useState(0);
  const [done, setDone] = useState(0);
  // Verify all: its question open, the call running, and what it did (or why it failed).
  const [confirmingAll, setConfirmingAll] = useState(false);
  const [verifyingAll, setVerifyingAll] = useState(false);
  const [allResult, setAllResult] = useState<{ text: string; failed: boolean } | null>(null);
  // Cancel gives the focus back to Verify all.
  const refocusVerifyAll = useRef(false);
  // The latest list, kept in step with every setList, so a resolve that lands
  // after another one works on the current queue.
  const latest = useRef<ReviewList | null>(null);
  // The element id to focus once the list has re-rendered.
  const focusNext = useRef<string | null>(null);
  // Opened for one document: its name, kept once known, so the way back still names it
  // after every value is settled and the list is empty.
  const [documentName, setDocumentName] = useState<string | null>(null);
  const back: Back | undefined = documentId
    ? { href: `/documents/${encodeURIComponent(documentId)}`, name: documentName ?? "the document" }
    : undefined;

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
        if (documentId && result.items[0]) setDocumentName(result.items[0].document_name);
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
    setAllResult(null);
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

  // Stable, so it focuses Cancel once, when the question opens (DocumentsTable's pattern).
  const focusOnMount = useCallback((element: HTMLButtonElement | null) => {
    element?.focus();
  }, []);

  const cancelVerifyAll = () => {
    refocusVerifyAll.current = true;
    setConfirmingAll(false);
  };

  /** One call for the whole queue the filters show; then the queue loads again, from the top. */
  const verifyAll = async () => {
    setVerifyingAll(true);
    setAllResult(null);
    try {
      const result = await verifyAllReview(verifyAllFilters(documentId, reason));
      setAllResult({ text: verifyAllText(result), failed: false });
    } catch (caught) {
      setAllResult({ text: `Could not verify all the values. ${messageOf(caught)}`, failed: true });
    } finally {
      setVerifyingAll(false);
      setConfirmingAll(false);
      setSaved("");
      setMoreError(null);
      setIndex(0);
      setDone(0);
      focusNext.current = VERIFY_ALL_RESULT_ID; // focused once the new list is shown
      setAttempt((n) => n + 1);
    }
  };

  // Review switched off for the whole product: a calm note in its place.
  if (failed === FEATURE_OFF) {
    return (
      <div className="page">
        <Header back={back} />
        <EmptyState icon="review" title={FEATURE_OFF} />
      </div>
    );
  }

  return (
    <div className="page">
      <Header back={back} />
      {/* PRD 9 F-17: the reasons in one Filter menu, the one shown said on its button. */}
      {values ? (
        <div className={styles.reasons}>
          <RowMenu
            name="the reasons"
            label={`Filter: ${reasonText(reason)}`}
            items={[{ value: null, text: "Any reason" }, ...REASON_FILTERS].map((choice) => ({
              label: choice.text,
              description: reason === choice.value ? "Shown now" : undefined,
              // A "Show more" still loading belongs to the old filter: wait for it. So does Verify all.
              disabledReason: loadingMore || verifyingAll ? "Wait until the list has loaded." : null,
              onSelect: () => {
                if (reason === choice.value) return;
                setMoreError(null);
                setAllResult(null);
                setConfirmingAll(false);
                setReason(choice.value);
                // In the address too, so a document opened from a card leads back to this list (#436).
                window.history.replaceState(null, "", reviewHref(documentId, choice.value));
                setIndex(0);
                setDone(0);
                setAttempt((n) => n + 1); // the loading state until the filtered list comes
              },
            }))}
          />
        </div>
      ) : null}
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
      {view === "document" ? <ReviewDocuments /> : null}
      <p className={announcement ? styles.saved : "visually-hidden"} aria-live="polite">
        {announcement ? <Icon name="check-seal" size={16} /> : null}
        {announcement}
      </p>
      {allResult ? (
        <p
          id={VERIFY_ALL_RESULT_ID}
          tabIndex={-1}
          className={allResult.failed ? "error-text" : styles.saved}
          role={allResult.failed ? "alert" : "status"}
        >
          {allResult.failed ? null : <Icon name="check-seal" size={16} />}
          {allResult.text}
        </p>
      ) : null}

      {values && failed && !loaded ? (
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

      {values && !loaded && !failed ? (
        <>
          <p className="visually-hidden" role="status">
            Loading the review queue…
          </p>
          <StillLoading onRetry={() => setAttempt((n) => n + 1)} />
          <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
            {SKELETON_CARDS.map((card) => (
              <span key={card} className={`skeleton ${styles.skeletonCard}`} />
            ))}
          </div>
        </>
      ) : null}

      {values && loaded ? (
        loaded.total === 0 ? (
          <EmptyState
            icon="check-seal"
            title={reason ? "Nothing to review for this reason" : "Nothing to review"}
            headingId={EMPTY_HEADING_ID}
            action={
              back ? (
                <Link href={back.href} className="btn">
                  {`Back to ${back.name}`}
                </Link>
              ) : (
                <Link href="/documents" className="btn">
                  Go to Documents
                </Link>
              )
            }
          >
            {reason
              ? "Choose Any reason to see the rest of the queue."
              : "Every value read so far is verified or already settled."}
          </EmptyState>
        ) : (
          <>
            <div className={styles.countRow}>
              <p className={`${styles.count} tabular`}>
                {view === "focus" && shown
                  ? `Value ${focusProgress(done, at, loaded.total)}`
                  : `${loaded.total} value${loaded.total === 1 ? "" : "s"} to review.`}
              </p>
              {canVerifyAll(me.user.role, loaded.total) && !confirmingAll ? (
                <button
                  ref={(element) => {
                    if (element && refocusVerifyAll.current) {
                      refocusVerifyAll.current = false;
                      element.focus();
                    }
                  }}
                  type="button"
                  className="btn btn-quiet"
                  disabled={loadingMore}
                  onClick={() => {
                    setAllResult(null);
                    setConfirmingAll(true);
                  }}
                >
                  <Icon name="check-seal" />
                  {`Verify all ${loaded.total}`}
                </button>
              ) : null}
            </div>
            {confirmingAll ? (
              <div
                className={styles.confirm}
                role="group"
                aria-label="Verify all"
                aria-describedby="review-verify-all-question"
              >
                <span id="review-verify-all-question">{verifyAllQuestion(loaded.total)}</span>
                {/* [Verify all N] [Cancel]; focus starts on Cancel. */}
                <button
                  type="button"
                  className={`btn btn-primary ${verifyingAll ? styles.busy : ""}`}
                  disabled={verifyingAll}
                  aria-busy={verifyingAll || undefined}
                  onClick={() => void verifyAll()}
                >
                  {verifyingAll && <span className="spinner" aria-hidden="true" />}
                  {`Verify all ${loaded.total}`}
                </button>
                <button ref={focusOnMount} type="button" className="btn" disabled={verifyingAll} onClick={cancelVerifyAll}>
                  <Icon name="close" />
                  Cancel
                </button>
                {verifyingAll ? (
                  <span className="muted" role="status">
                    {`Verifying ${loaded.total} value${loaded.total === 1 ? "" : "s"}…`}
                  </span>
                ) : null}
              </div>
            ) : null}
            {/* Locked while Verify all runs: nothing in the queue can be pressed or focused. */}
            <div className={styles.lockable} inert={verifyingAll} aria-busy={verifyingAll || undefined}>
              {view === "focus" ? (
                shown ? (
                  <ReviewCard key={shown.value.id} item={shown} onResolved={onResolved} onSkip={skip} />
                ) : (
                  <p className="muted">Loading the next value…</p>
                )
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
            </div>
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
  const reason = parseReason(searchParams.get("reason") ?? "");
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
  return (
    <BackFrom value={reviewHref(documentId, reason)}>
      <Queue documentId={documentId} initialReason={reason} />
    </BackFrom>
  );
}
