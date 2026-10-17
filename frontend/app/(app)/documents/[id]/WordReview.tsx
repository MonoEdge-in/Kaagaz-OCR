"use client";

// Word-level review on the document screen (scan review UX rules, Tushar, 16 Oct 2026,
// sections 3, 5 and 7). useWordReview loads the review status and the flagged words
// together, and while an Ensemble run is queued or running asks again on the shared poll
// steps (lib/poll.ts, never more than 5 s), stopping once it ends. The summary bar sits
// above the document: the scan's sentence, the review progress, the run's progress
// (aria-live, also in the status bar) or its result, and its actions with the primary
// last: More (Run Ensemble on full document, Export, Download original), Run Ensemble
// on flagged words (only when the API allows it; while scanning, disabled with its
// reason) and Review flagged words. Ensemble asks first for the full document or more
// than 50 words, with the page count and time estimate. The export dialog states the
// review status with both choices equally visible; the app has no export file, so
// Export downloads the original and says so.

import { useCallback, useEffect, useId, useState } from "react";
import type { RefObject } from "react";
import { RowMenu } from "@/components/admin/RowMenu";
import type { RowMenuItem } from "@/components/admin/RowMenu";
import { ActionDialog } from "@/components/documents/ActionDialog";
import libraryStyles from "@/components/documents/Library.module.css";
import {
  ABOUT_ENSEMBLE,
  CHANGES_NOTE,
  CONTINUE_LABEL,
  DOWNLOAD_LABEL,
  ENSEMBLE_DOCUMENT,
  ENSEMBLE_FLAGGED,
  ENSEMBLE_INFO,
  EXPORT_ANYWAY,
  EXPORT_LABEL,
  EXPORT_NOTE,
  EXPORT_WAIT,
  KEEP_ALL,
  REVIEW_FIRST,
  REVIEW_LABEL,
  SHOW_FLAGS,
  confirmText,
  doneCount,
  failureText,
  finishedText,
  keepAll,
  needsConfirm,
  openChanges,
  progressText,
  resultText,
  retryLabel,
  retryRun,
  runActive,
  runEnsemble,
  runText,
  stopRun,
  summaryText,
} from "@/components/review/wordReview";
import { InStatusBar } from "@/components/shell/StatusBar";
import { Icon } from "@/components/ui/Icon";
import { getEnsembleEstimate, getReviewStatus, listFlags } from "@/lib/api";
import type { EnsembleEstimate, EnsembleRun, EnsembleScope, ReviewStatus, WordFlag } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";
import styles from "./WordReview.module.css";

type Loaded = { status: ReviewStatus; flags: WordFlag[] };

export interface WordReview {
  status: ReviewStatus | null;
  flags: WordFlag[];
  failed: boolean;
  reload(): void;
  /** A confirm or edit reply: that word as it is now (the counts come with the next load). */
  replace(flag: WordFlag): void;
  /** A run just started, stopped or retried: show it, and follow it while it runs. */
  setRun(run: EnsembleRun): void;
}

const following = (loaded: Loaded) => runActive(loaded.status.ensemble.run);

/** enabled: false asks nothing (a rejected file has no words to review). */
export function useWordReview(documentId: string, version: number, enabled = true): WordReview {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [failed, setFailed] = useState(false);
  const [asks, setAsks] = useState(0);
  const load = useCallback(
    () =>
      Promise.all([getReviewStatus(documentId), listFlags(documentId)]).then(([status, flags]) => ({
        status,
        flags: flags.items,
      })),
    [documentId],
  );
  const poll = usePoll<Loaded>({
    load,
    onResult: setLoaded,
    onFailure: () => undefined, // it keeps trying; the bar shows what it has
    again: following,
    signature: ({ status }) => {
      const run = status.ensemble.run;
      return `${status.status}|${run?.id}|${run?.status}|${run?.pages_done}`;
    },
  });

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    load().then(
      (next) => {
        if (cancelled) return;
        setLoaded(next);
        setFailed(false);
        if (following(next)) poll.later();
      },
      () => {
        if (!cancelled) setFailed(true);
      },
    );
    return () => {
      cancelled = true;
      poll.stop();
    };
  }, [load, poll, version, asks, enabled]);

  return {
    status: loaded?.status ?? null,
    flags: loaded?.flags ?? [],
    failed,
    reload: () => setAsks((n) => n + 1),
    replace: (flag) =>
      setLoaded((now) =>
        now ? { ...now, flags: now.flags.map((each) => (each.word_key === flag.word_key ? flag : each)) } : now,
      ),
    setRun: (run) => {
      setLoaded((now) =>
        now ? { ...now, status: { ...now.status, ensemble: { ...now.status.ensemble, run } } } : now,
      );
      if (runActive(run)) poll.later();
      else setAsks((n) => n + 1);
    },
  };
}

/**
 * Starting Ensemble on a scope: one word at once; otherwise the estimate first, and the
 * confirm sheet for the full document or more than 50 words. `sheet` is the sheet to draw.
 */
export function useEnsembleStart(documentId: string, review: WordReview, say: (message: string) => void) {
  const [asking, setAsking] = useState<{ scope: EnsembleScope; keys: string[]; estimate: EnsembleEstimate } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function start(scope: EnsembleScope, keys: string[]) {
    setBusy(true);
    const done = await runEnsemble(documentId, scope, keys);
    setBusy(false);
    setAsking(null);
    if (done.ok) {
      review.setRun(done.value);
      say(done.message);
    } else setError(done.message);
  }

  async function ask(scope: EnsembleScope, keys: string[] = []) {
    setError(null);
    if (scope === "word") return start(scope, keys);
    try {
      const estimate = await getEnsembleEstimate(documentId, scope, keys);
      if (needsConfirm(scope, estimate.words)) setAsking({ scope, keys, estimate });
      else await start(scope, keys);
    } catch (caught) {
      setError(failureText(caught));
    }
  }

  const sheet = asking && (
    <ActionDialog title="Run Ensemble" onClose={() => setAsking(null)} canClose={() => !busy}>
      <div className={libraryStyles.dialogForm}>
        <p>{confirmText(asking.scope, asking.estimate)}</p>
        <div className="actions">
          <button type="button" className="btn" onClick={() => setAsking(null)} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            data-autofocus
            aria-busy={busy || undefined}
            disabled={busy}
            onClick={() => void start(asking.scope, asking.keys)}
          >
            Run Ensemble
          </button>
        </div>
      </div>
    </ActionDialog>
  );
  return { ask, busy, error, sheet };
}

/** The info popover (section 5): what Ensemble does, in the owner's words. */
export function EnsembleInfo() {
  const id = useId();
  const anchor = `--ensemble-info-${id.replace(/[^\w-]/g, "")}`;
  return (
    <>
      <button
        type="button"
        className={`btn btn-quiet ${styles.info}`}
        popoverTarget={id}
        aria-label={ABOUT_ENSEMBLE}
        title={ABOUT_ENSEMBLE}
        style={{ anchorName: anchor }}
      >
        <span aria-hidden="true">i</span>
      </button>
      <div id={id} popover="auto" role="note" className={`motion-pop ${styles.popover}`} style={{ positionAnchor: anchor }}>
        <p>{ENSEMBLE_INFO}</p>
      </div>
    </>
  );
}

export interface SummaryBarProps {
  documentId: string;
  review: WordReview;
  scanning: string; // the document's own status words while it is read
  reviewing: boolean;
  reviewRef: RefObject<HTMLButtonElement | null>;
  onReview(): void;
  onEnsemble(scope: EnsembleScope): void;
  ensembleBusy: boolean;
  ensembleError: string | null;
  onExport(): void;
  download: string | null;
  say(message: string): void;
}

export function SummaryBar(props: SummaryBarProps) {
  const { documentId, review, scanning, reviewing, reviewRef, onReview, onEnsemble, ensembleBusy, ensembleError } = props;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const status = review.status;
  if (!status) {
    return review.failed ? (
      <div className={styles.bar} role="alert">
        <p className={styles.text}>The flagged words could not be loaded. Nothing was changed.</p>
        <div className={styles.actions}>
          <button type="button" className="btn" onClick={review.reload}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      </div>
    ) : (
      <div className={`skeleton ${styles.placeholder}`} aria-hidden="true" /> // holds its place: the page never jumps
    );
  }

  const ready = status.status === "ready";
  const run = status.ensemble.run;
  const active = run !== null && runActive(run);
  const done = doneCount(status);
  // Ensemble: offered when the API allows it; while scanning, shown disabled with its reason.
  const ensembleShown = status.ensemble.allowed || (!ready && status.ensemble.reason !== null && status.can_review);
  const waitReason = status.ensemble.allowed ? null : status.ensemble.reason;
  const act = async (call: () => Promise<{ ok: boolean; message: string }>, after: () => void) => {
    setBusy(true);
    setError(null);
    const result = await call();
    setBusy(false);
    if (result.ok) {
      props.say(result.message);
      after();
    } else setError(result.message);
  };

  const more: RowMenuItem[] = [
    ...(ensembleShown && !active
      ? [{ label: ENSEMBLE_DOCUMENT, onSelect: () => onEnsemble("document"), disabledReason: waitReason }]
      : []),
    { label: EXPORT_LABEL, onSelect: props.onExport, disabledReason: ready ? null : EXPORT_WAIT },
    ...(props.download
      ? [{ label: DOWNLOAD_LABEL, onSelect: () => downloadFile(props.download ?? "") }]
      : []),
  ];

  return (
    <section className={styles.bar} aria-label="Scan summary">
      <div className={styles.text}>
        <p className={`${styles.summary} tabular`}>{summaryText(status, scanning)}</p>
        {ready && status.flagged > 0 && (
          <p className={`${styles.line} tabular`}>
            {status.remaining === 0 ? finishedText(status.flagged) : progressText(done, status.flagged)}
          </p>
        )}
        <p className={`${styles.line} tabular`} aria-live="polite">
          {active ? runText(run) : ""}
        </p>
        {run && !active && (
          <div className={styles.result}>
            <p className={styles.line}>
              {run.error ? run.error.message : resultText(run)}
              {!run.error && openChanges(run) > 0 && status.can_review ? ` ${CHANGES_NOTE}` : ""}
            </p>
            {run.error && run.failed_pages.length > 0 && status.ensemble.allowed && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() =>
                  void act(
                    () =>
                      retryRun(documentId, run.id).then((result) => {
                        if (result.ok) review.setRun(result.value);
                        return result;
                      }),
                    () => undefined,
                  )
                }
              >
                {retryLabel(run.failed_pages)}
              </button>
            )}
            {!run.error && openChanges(run) > 0 && status.can_review && (
              <button type="button" className="btn" disabled={busy} onClick={() => void act(() => keepAll(documentId, run.id), review.reload)}>
                {KEEP_ALL}
              </button>
            )}
          </div>
        )}
        {(error ?? ensembleError) && (
          <p className="error-text" role="alert">
            {error ?? ensembleError}
          </p>
        )}
      </div>
      <div className={styles.actions}>
        <RowMenu name="this document" items={more} />
        {active ? (
          status.can_review && (
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() =>
                void act(
                  () =>
                    stopRun(documentId, run.id).then((result) => {
                      if (result.ok) review.setRun(result.value);
                      return result;
                    }),
                  () => undefined,
                )
              }
            >
              Stop
            </button>
          )
        ) : ensembleShown && (status.remaining > 0 || !ready) ? (
          <span className={styles.ensemble}>
            <button
              type="button"
              className="btn"
              aria-disabled={waitReason ? true : undefined}
              aria-describedby={waitReason ? `${documentId}-ensemble-wait` : undefined}
              aria-busy={ensembleBusy || undefined}
              onClick={() => {
                if (!waitReason && !ensembleBusy) onEnsemble("flagged");
              }}
            >
              {ENSEMBLE_FLAGGED}
            </button>
            <EnsembleInfo />
            {waitReason && (
              <span id={`${documentId}-ensemble-wait`} className={styles.reason}>
                {waitReason}
              </span>
            )}
          </span>
        ) : null}
        {ready && status.flagged > 0 && (
          <button
            ref={reviewRef}
            type="button"
            className="btn btn-primary"
            aria-pressed={reviewing}
            onClick={onReview}
          >
            {!status.can_review ? SHOW_FLAGS : done > 0 && status.remaining > 0 ? CONTINUE_LABEL : REVIEW_LABEL}
          </button>
        )}
      </div>
      {active && (
        <InStatusBar>
          <span className="tabular">{runText(run)}</span>
        </InStatusBar>
      )}
    </section>
  );
}

/** The original file, as the header's Download original link does (a download, never a page change). */
function downloadFile(href: string) {
  const link = document.createElement("a");
  link.href = href;
  link.download = "";
  link.click();
}

/** Section 7: the review status, and both choices equally visible. */
export function ExportDialog({
  status,
  download,
  onReview,
  onClose,
}: {
  status: ReviewStatus;
  download: string | null;
  onReview(): void;
  onClose(): void;
}) {
  const exportButton = (label: string) =>
    download ? (
      <a className="btn" href={download} download onClick={() => setTimeout(onClose)}>
        {label}
      </a>
    ) : null;
  return (
    <ActionDialog title={EXPORT_LABEL} onClose={onClose}>
      <div className={libraryStyles.dialogForm}>
        <p className="tabular">{status.export.message}</p>
        <p className="muted">{EXPORT_NOTE}</p>
        <div className="actions">
          {status.export.ready ? (
            <>
              <button type="button" className="btn" data-autofocus onClick={onClose}>
                Cancel
              </button>
              {exportButton(EXPORT_LABEL)}
            </>
          ) : (
            <>
              <button type="button" className="btn" data-autofocus onClick={onReview}>
                {REVIEW_FIRST}
              </button>
              {exportButton(EXPORT_ANYWAY)}
            </>
          )}
        </div>
      </div>
    </ActionDialog>
  );
}
