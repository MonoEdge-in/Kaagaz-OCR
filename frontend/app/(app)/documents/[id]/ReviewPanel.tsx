"use client";

// The review panel (scan review UX rules, Tushar, 16 Oct 2026, sections 4, 5 and 7): the
// Needs attention tab while reviewing, a bottom sheet on a phone. For the word being
// reviewed: a crop of the page around it (12px clear, zoomable), its band, reason and
// exact score, the reading editable inline, up to three other readings as one-tap
// options (reviewers and admins only: the API sends none to a member), and Confirm
// (primary, last), Edit, Send to Ensemble and Skip, with "4 of 12 reviewed" and Previous
// and Next. Keys: N and Shift+N, Enter, E, S, Esc; never while typing, in a dialog or in a
// menu (reviewKey). Focus comes here when it opens; DocumentView puts it back on the word.
// A change Ensemble proposes shows before and after with Keep and Revert; a word Ensemble
// could not settle shows every reading. A 409 says the API's words and loads the word
// again. Under it, the flagged words with checkboxes (Shift+click picks a run of them) to
// send to Ensemble, and a History view: every change, who and when, Undo where allowed.
// Read this spot again (#385, reviewers and admins): only the word's box of its page is read
// again, cut out and zoomed; the document goes back to be read, so review ends.

import { useEffect, useEffectEvent, useRef, useState } from "react";
import type { RefObject } from "react";
import { FlagText } from "@/components/review/FlagText";
import { Icon } from "@/components/ui/Icon";
import {
  EXIT_LABEL,
  HISTORY_FILTERS,
  IN_ENSEMBLE,
  SEND_TO_ENSEMBLE,
  bandLine,
  changeTitle,
  decide,
  doneCount,
  doneText,
  failureText,
  finishedText,
  nextOpen,
  progressText,
  proposal,
  READ_SPOT_AGAIN,
  READ_SPOT_SENT,
  readingsDifferText,
  readingsOf,
  reviewKey,
  saveWord,
  scoreText,
  selectRange,
  spotOf,
  stateText,
  undoWord,
  unsettled,
} from "@/components/review/wordReview";
import type { HistoryFilter } from "@/components/review/wordReview";
import { PageViewer } from "@/components/viewer/PageViewer";
import { fromServer, turnOf } from "@/components/viewer/viewer";
import { listChanges, readPageAgain } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { DocumentDetail, DocumentSummary, EnsembleScope, WordChange } from "@/lib/types";
import type { WordReview } from "./WordReview";
import styles from "./WordReview.module.css";

const CROP_HEIGHTS = [176, 352] as const; // the crop, and zoomed in

export interface ReviewPanelProps {
  detail: DocumentDetail;
  review: WordReview;
  current: string | null; // the word being reviewed
  onCurrent(key: string): void;
  onExit(): void;
  ensembleAllowed: boolean;
  onEnsemble(scope: EnsembleScope, keys: string[]): void;
  say(message: string): void;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onReadAgain(reply: DocumentSummary): void; // #385: the word's spot sent to be read again
}

export function ReviewPanel(props: ReviewPanelProps) {
  const { detail, review, current, onCurrent, onExit, ensembleAllowed, onEnsemble, say, headingRef, onReadAgain } =
    props;
  const status = review.status;
  const flags = review.flags;
  const index = Math.max(0, flags.findIndex((flag) => flag.word_key === current));
  const flag = flags[index];
  const canReview = status?.can_review ?? false;
  const finished = status !== null && status.flagged > 0 && status.remaining === 0;
  const canEdit = canReview && !finished;
  const [view, setView] = useState<"words" | "history">("words");
  const [editing, setEditing] = useState<{ key: string; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zoomed, setZoomed] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const lastPicked = useRef<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => headingRef.current?.focus(), [headingRef]);

  const go = (step: number) => {
    const next = flags[(index + step + flags.length) % flags.length];
    if (!next) return;
    setEditing(null);
    setError(null);
    onCurrent(next.word_key);
  };

  async function act(call: () => Promise<{ ok: boolean; message: string; reload?: boolean }>, advance: boolean) {
    setBusy(true);
    setError(null);
    const done = await call();
    setBusy(false);
    if (!done.ok) {
      setError(done.message);
      if (done.reload) review.reload(); // changed since it was opened: show the word as it is now
      return;
    }
    say(done.message);
    setEditing(null);
    if (advance) {
      const next = nextOpen(flags, index);
      if (next >= 0 && next !== index) onCurrent(flags[next]?.word_key ?? "");
    }
    review.reload(flag);
  }

  const save = (text: string | null) => {
    if (!flag || busy) return;
    const typed = text?.trim() ?? null;
    if (typed === "") return setError("Type the correct text.");
    void act(() => saveWord(detail.id, flag, typed === flag.text ? null : typed), true);
  };
  const startEdit = () => {
    if (!flag || !canEdit) return;
    setEditing({ key: flag.word_key, text: flag.text });
    requestAnimationFrame(() => inputRef.current?.select());
  };
  const readSpotAgain = () => {
    const spot = flag ? spotOf(flag) : null;
    if (!flag || !spot || !canEdit || busy) return;
    setBusy(true);
    setError(null);
    readPageAgain(detail.id, flag.page_no, [spot]).then(
      (reply) => {
        setBusy(false);
        say(READ_SPOT_SENT);
        onReadAgain(reply);
      },
      (caught: unknown) => {
        setBusy(false);
        setError(failureText(caught));
      },
    );
  };
  const sendWord = () => {
    if (flag && canEdit && ensembleAllowed) onEnsemble("word", [flag.word_key]);
  };

  const onKey = useEffectEvent((event: KeyboardEvent) => {
    const target = event.target instanceof HTMLElement ? event.target : null;
    const action = reviewKey({
      key: event.key,
      shiftKey: event.shiftKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      altKey: event.altKey,
      target: target && {
        tagName: target.tagName,
        isContentEditable: target.isContentEditable,
        inOverlay: target.closest("dialog, [popover]") !== null,
      },
    });
    if (action === null || view !== "words") return;
    event.preventDefault();
    if (action === "next") go(1);
    else if (action === "previous") go(-1);
    else if (action === "exit") onExit();
    else if (action === "confirm" && canEdit) save(null);
    else if (action === "edit" && canEdit) startEdit();
    else if (action === "ensemble" && canEdit) sendWord();
  });
  useEffect(() => {
    const listener = (event: KeyboardEvent) => onKey(event);
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);

  if (!status) return null;
  const done = doneCount(status);
  const page = flag ? detail.pages.find((each) => each.page_no === flag.page_no) : undefined;
  const changed = flag ? proposal(flag) : null;
  const options = flag ? (unsettled(flag) ? readingsOf(flag) : (flag.alternatives ?? [])) : [];
  const draft = editing && flag && editing.key === flag.word_key ? editing.text : null;
  const after = flag ? doneText(flag) : null;
  const score = flag ? scoreText(flag.score) : null;
  const selectable = canEdit && ensembleAllowed;
  const keys = flags.map((each) => each.word_key);

  return (
    <section className={styles.review} aria-labelledby="review-heading">
      <div className={styles.reviewHead}>
        <h2 id="review-heading" ref={headingRef} tabIndex={-1} className={styles.heading}>
          {finished ? "Verified" : canReview ? "Review flagged words" : "Flagged words"}
        </h2>
        <p className={`${styles.line} tabular`} aria-live="polite">
          {progressText(done, status.flagged)}
        </p>
        <div className={styles.headActions}>
          <button type="button" className="btn btn-quiet" aria-pressed={view === "history"} onClick={() => setView(view === "history" ? "words" : "history")}>
            History
          </button>
          <button type="button" className="btn" onClick={onExit}>
            {EXIT_LABEL}
          </button>
        </div>
      </div>
      {!canReview && <p className={`muted ${styles.line}`}>Only a reviewer or an admin can confirm or edit these words.</p>}

      {view === "history" ? (
        <HistoryPanel
          key={status.last_change_at ?? ""}
          documentId={detail.id}
          say={say}
          onUndone={review.reload}
        />
      ) : (
        <>
          {finished && (
            <p className={styles.finished} role="status">
              {finishedText(status.flagged)}
            </p>
          )}
          {flag && (
            <div key={flag.word_key} className={`motion-next ${styles.word}`}>
              {page && (
                <div className={styles.crop}>
                  <PageViewer
                    document={detail}
                    page={page}
                    highlights={[flag.region]}
                    crop={flag.region}
                    cropMaxHeight={CROP_HEIGHTS[zoomed ? 1 : 0]}
                    turn={turnOf(fromServer(detail.page_turns ?? []), page.page_no)}
                  />
                  <button type="button" className="btn btn-quiet" aria-pressed={zoomed} onClick={() => setZoomed(!zoomed)}>
                    {zoomed ? "Zoom out" : "Zoom in"}
                  </button>
                </div>
              )}
              <p className={styles.reading}>
                <FlagText flag={flag} />
                <span className="muted"> · page {flag.page_no}</span>
                {flag.state === "in_ensemble" && <span className={styles.inEnsemble}>{IN_ENSEMBLE}</span>}
              </p>
              <p className={styles.band}>
                {bandLine(flag)}
                {score && <span className={`${styles.score} tabular`}> · {score}</span>}
              </p>
              {changed && (
                <div className={styles.change}>
                  <p>
                    Ensemble reads <bdi className="mono">&apos;{changed.after}&apos;</bdi>, the text now is{" "}
                    <bdi className="mono">&apos;{changed.before}&apos;</bdi>.
                  </p>
                  <p className="mono" aria-hidden="true">
                    {changed.before} → {changed.after}
                  </p>
                  {canEdit && flag.ensemble && (
                    <div className={styles.row}>
                      <button type="button" className="btn" disabled={busy} onClick={() => void act(() => decide(detail.id, flag.ensemble?.reading_id ?? "", false), false)}>
                        Revert
                      </button>
                      <button type="button" className="btn" disabled={busy} onClick={() => void act(() => decide(detail.id, flag.ensemble?.reading_id ?? "", true), true)}>
                        Keep
                      </button>
                    </div>
                  )}
                </div>
              )}
              {canEdit && unsettled(flag) && <p className={styles.line}>{readingsDifferText(options)}</p>}
              {canEdit && (
                <>
                  <label className={styles.field}>
                    <span className={styles.fieldLabel}>Reading</span>
                    <input
                      ref={inputRef}
                      className={`input mono ${styles.input}`}
                      type="text"
                      autoComplete="off"
                      spellCheck={false}
                      readOnly={draft === null}
                      value={draft ?? flag.text}
                      onChange={(event) => setEditing({ key: flag.word_key, text: event.target.value })}
                      onFocus={() => draft === null && startEdit()}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && draft !== null) {
                          event.preventDefault();
                          save(draft);
                        } else if (event.key === "Escape" && draft !== null) {
                          event.preventDefault();
                          event.stopPropagation();
                          setEditing(null);
                        }
                      }}
                    />
                  </label>
                  {options.length > 0 && (
                    <div className={styles.options} role="group" aria-label="Other readings">
                      {options.map((text) => (
                        <button key={text} type="button" className="btn mono" disabled={busy} onClick={() => save(text)}>
                          <bdi>{text}</bdi>
                        </button>
                      ))}
                    </div>
                  )}
                  <div className={styles.row}>
                    {draft !== null ? (
                      <>
                        <button type="button" className="btn" onClick={() => setEditing(null)}>
                          Cancel
                        </button>
                        <button type="button" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined} onClick={() => save(draft)}>
                          Save
                        </button>
                      </>
                    ) : (
                      <>
                        <button type="button" className="btn btn-quiet" onClick={() => go(1)}>
                          Skip
                        </button>
                        {ensembleAllowed && (
                          <button type="button" className="btn" onClick={sendWord}>
                            {SEND_TO_ENSEMBLE}
                          </button>
                        )}
                        {spotOf(flag) && (
                          <button type="button" className="btn" disabled={busy} onClick={readSpotAgain}>
                            {READ_SPOT_AGAIN}
                          </button>
                        )}
                        <button type="button" className="btn" onClick={startEdit}>
                          Edit
                        </button>
                        <button type="button" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined} onClick={() => save(null)}>
                          Confirm
                        </button>
                      </>
                    )}
                  </div>
                  <p className={styles.keys}>Keys: N next, Shift+N previous, Enter confirm, E edit, S send to Ensemble, Esc exit.</p>
                </>
              )}
              {after && (
                <p className={styles.line}>
                  {after}
                  {canReview && flag.change && (
                    <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => void act(() => undoWord(detail.id, flag.change?.id ?? ""), false)}>
                      Undo
                    </button>
                  )}
                </p>
              )}
              {error && (
                <p className="error-text" role="alert">
                  {error}
                </p>
              )}
              <div className={styles.row}>
                <button type="button" className="btn" onClick={() => go(-1)} disabled={flags.length < 2}>
                  Previous
                </button>
                <span className="tabular muted">
                  Word {index + 1} of {flags.length}
                </span>
                <button type="button" className="btn" onClick={() => go(1)} disabled={flags.length < 2}>
                  Next
                </button>
              </div>
            </div>
          )}

          {/* #432: the list starts shut, one row with its count; it stays as the person left it
              while they work through the words (the panel stays mounted). */}
          <details className={styles.listToggle}>
            <summary className={styles.listSummary}>
              <Icon name="chevron-right" size={16} className={styles.listChevron} />
              Flagged words <span className="tabular">({flags.length.toLocaleString("en-IN")})</span>
            </summary>
            <ul className={styles.list} aria-label="Flagged words">
              {flags.map((each) => (
                <li key={each.word_key} className={styles.item} aria-current={each.word_key === flag?.word_key ? "true" : undefined}>
                  {selectable && (
                    <input
                      type="checkbox"
                      aria-label={`Select '${each.text}' for Ensemble`}
                      checked={selected.has(each.word_key)}
                      onChange={(event) => {
                        const native = event.nativeEvent as MouseEvent;
                        const from = native.shiftKey ? lastPicked.current : null;
                        setSelected(selectRange(keys, selected, from, each.word_key, event.target.checked));
                        lastPicked.current = each.word_key;
                      }}
                    />
                  )}
                  <button type="button" className={styles.itemButton} onClick={() => onCurrent(each.word_key)}>
                    <FlagText flag={each} />
                    <span className={styles.itemMeta}>
                      Page {each.page_no} · {stateText(each)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </details>
          {selectable && selected.size > 0 && (
            <div className={styles.selection}>
              <span className="tabular">{selected.size.toLocaleString("en-IN")} selected</span>
              <button type="button" className="btn btn-quiet" onClick={() => setSelected(new Set())}>
                Clear
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  onEnsemble("words", [...selected]);
                  setSelected(new Set());
                }}
              >
                Run Ensemble on selected words
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

/** Every change, newest first: who, when, before and after, Undo where the API allows it. */
export function HistoryPanel({
  documentId,
  say,
  onUndone,
}: {
  documentId: string;
  say(message: string): void;
  onUndone(): void;
}) {
  const [filter, setFilter] = useState<HistoryFilter>("all");
  const [shown, setShown] = useState<{ filter: HistoryFilter; items: WordChange[]; next: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [asks, setAsks] = useState(0);
  const source = filter === "all" ? undefined : filter;

  useEffect(() => {
    let cancelled = false;
    listChanges(documentId, { source }).then(
      (page) => {
        if (!cancelled) setShown({ filter, items: page.items, next: page.next_before });
      },
      (caught: unknown) => {
        if (!cancelled) setError(failureText(caught));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [documentId, filter, source, asks]);

  const items = shown && shown.filter === filter ? shown : null;
  const older = (before: string) =>
    listChanges(documentId, { source, before }).then(
      (page) => setShown((now) => now && { ...now, items: [...now.items, ...page.items], next: page.next_before }),
      (caught: unknown) => setError(failureText(caught)),
    );
  const undo = async (change: WordChange) => {
    const done = await undoWord(documentId, change.id);
    if (!done.ok) return setError(done.message);
    say(done.message);
    setAsks((n) => n + 1);
    onUndone();
  };

  return (
    <div className={styles.history}>
      <fieldset className={styles.filters}>
        <legend className="visually-hidden">Show changes</legend>
        {HISTORY_FILTERS.map((choice) => (
          <label key={choice.id}>
            <input type="radio" name="history-filter" checked={filter === choice.id} onChange={() => setFilter(choice.id)} />
            {choice.label}
          </label>
        ))}
      </fieldset>
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {!items ? (
        <span className={`skeleton ${styles.skeleton}`} aria-hidden="true" />
      ) : items.items.length === 0 ? (
        <p className="muted">No changes yet. Words you confirm or edit, and words Ensemble resolves, show here.</p>
      ) : (
        <ol className={styles.list}>
          {items.items.map((change) => (
            <li key={change.id} className={`motion-fade ${styles.change}`}>
              <p>
                <strong>{changeTitle(change)}</strong>
                {change.created_at && <span className="muted"> · {formatDateTime(change.created_at)}</span>}
                <span className="muted"> · page {change.page_no}</span>
              </p>
              <p className="mono">
                <bdi>{change.before_text}</bdi> → <bdi>{change.after_text}</bdi>
              </p>
              {change.undone_at && <p className="muted">Undone · {formatDateTime(change.undone_at)}</p>}
              {change.note && <p className="muted">{change.note}</p>}
              {change.can_undo && !change.undone_at && (
                <button type="button" className="btn btn-quiet" onClick={() => void undo(change)}>
                  Undo
                </button>
              )}
            </li>
          ))}
        </ol>
      )}
      {items?.next && (
        <button type="button" className="btn" onClick={() => void older(items.next ?? "")}>
          Show older changes
        </button>
      )}
    </div>
  );
}
