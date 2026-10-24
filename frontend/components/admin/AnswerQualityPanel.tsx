"use client";

// Admin → Answer quality (PRD 6 F2, decision 7): the answers people marked Not useful after
// the form told them their company admin can see it, newest first: who asked, the question,
// the answer as Ask shows it (read-only), what went wrong, their words, and when. Open or
// Handled, over the last 4 or 12 weeks; Useful and Not useful counts for each week, as
// numbers. Mark as handled takes the answer off the Open list at once, with Undo for 8
// seconds (the shared Toast, focused); it is sent once Undo has gone. Everything is shown as plain text; the only links are the
// answer's own source links, built from ids. Cards in one column below 48rem.

import { useEffect, useRef, useState } from "react";
import type { Ref } from "react";
import { AnswerView } from "@/components/chat/AnswerView";
import { REASONS } from "@/components/chat/answer";
import { pageInfo } from "@/components/documents/logic";
import { Icon } from "@/components/ui/Icon";
import { Segmented } from "@/components/ui/Segmented/Segmented";
import { Skeleton } from "@/components/ui/Skeleton";
import { Toast } from "@/components/ui/Toast";
import { getAnswerQuality, setAnswerHandled } from "@/lib/api";
import { formatDate, formatDateTime, formatNumber } from "@/lib/format";
import type { AnswerQualityItem, AnswerQualityReply, Message, ReasonCode } from "@/lib/types";
import { retryErrorText } from "./logic";
import styles from "./Admin.module.css";
import quality from "./AnswerQuality.module.css";

export type QualityStatus = "open" | "handled";
export type QualityWeeks = "4" | "12";
export const QUALITY_PAGE_SIZE = 50; // the server sends at most 100

export const QUALITY_TEXT = {
  heading: "Answer quality",
  intro: "Answers people marked Not useful after they were told you can see them.",
  emptyOpen: "No answers marked Not useful in this period.",
  emptyHandled: "No handled answers in this period.",
  loading: "Loading the answers…",
  failed: "Could not load the answers.",
  weeks: "Useful and Not useful by week",
  handle: "Mark as handled",
  reopen: "Open again",
  handled: "Marked as handled.",
  reopened: "Opened again.",
  noReason: "No reason ticked.",
} as const;

const SHOW: { value: QualityStatus; label: string }[] = [{ value: "open", label: "Open" }, { value: "handled", label: "Handled" }];
const PERIOD: { value: QualityWeeks; label: string }[] = [{ value: "4", label: "Last 4 weeks" }, { value: "12", label: "Last 12 weeks" }];

/** The asker's answer as Ask's answer card takes it; no rating of the admin's own. */
const answerMessage = (item: AnswerQualityItem): Message =>
  ({ id: item.message_id, role: "assistant", text: "", created_at: item.given_at, answer: item.answer, feedback: null });

/** The reply without one item, its total one less. */
export function withoutItem(data: AnswerQualityReply, id: string): AnswerQualityReply {
  return { ...data, items: data.items.filter((each) => each.id !== id), total: Math.max(0, data.total - 1) };
}

/**
 * Mark as handled or Open again, held until its Undo has gone: ``flush`` sends it (the toast
 * closing, another one starting, or the panel closing); ``undo`` drops it, so Undo never has to
 * put a handled time or admin back on the server. ``onFail`` hears a refusal.
 */
export function deferred(send: (item: AnswerQualityItem, handled: boolean) => Promise<unknown>) {
  let waiting: { item: AnswerQualityItem; handled: boolean; onFail(caught: unknown): void } | null = null;
  const flush = () => {
    const now = waiting;
    waiting = null;
    if (now) send(now.item, now.handled).catch(now.onFail);
  };
  return {
    start(item: AnswerQualityItem, handled: boolean, onFail: (caught: unknown) => void) {
      flush();
      waiting = { item, handled, onFail };
    },
    undo() {
      waiting = null;
    },
    flush,
  };
}

/** What went wrong, in the form's words: "Wrong figure, Other". */
export function reasonsText(codes: ReasonCode[]): string {
  const label = (code: ReasonCode) => REASONS.find((reason) => reason.code === code)?.label ?? code;
  return codes.length > 0 ? codes.map(label).join(", ") : QUALITY_TEXT.noReason;
}

export function AnswerQualityPanel() {
  const [status, setStatus] = useState<QualityStatus>("open");
  const [weeks, setWeeks] = useState<QualityWeeks>("4");
  const [offset, setOffset] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const [reply, setReply] = useState<{ key: string; data: AnswerQualityReply } | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [toast, setToast] = useState<{ text: string; undo?: () => void } | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const key = `${status}:${weeks}:${offset}:${attempt}`;

  useEffect(() => {
    let cancelled = false;
    getAnswerQuality({ status, weeks: Number(weeks), limit: QUALITY_PAGE_SIZE, offset }).then(
      (data) => {
        if (!cancelled) setReply({ key, data });
      },
      (caught: unknown) => {
        if (!cancelled) setError({ key, message: retryErrorText(caught) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [status, weeks, offset, key]);

  const choose = (next: { status?: QualityStatus; weeks?: QualityWeeks; offset?: number }) => {
    if (next.status) setStatus(next.status);
    if (next.weeks) setWeeks(next.weeks);
    setOffset(next.offset ?? 0);
    headingRef.current?.focus();
  };

  const [queue] = useState(() => deferred((item: AnswerQualityItem, handled: boolean) => setAnswerHandled(item.id, handled, item.given_at)));
  useEffect(() => () => queue.flush(), [queue]); // leaving the tab sends what is waiting

  // The answer leaves this list at once and is sent when its Undo (8 s) has gone; Undo puts
  // it back. A refusal (a new rating since, 409) puts it back and says so.
  const handle = (item: AnswerQualityItem) => {
    const before = reply;
    if (reply) setReply({ ...reply, data: withoutItem(reply.data, item.id) });
    queue.start(item, status === "open", (caught) => {
      setReply(before);
      setToast({ text: retryErrorText(caught) });
    });
    setToast({
      text: status === "open" ? QUALITY_TEXT.handled : QUALITY_TEXT.reopened,
      undo: () => {
        queue.undo();
        setReply(before);
      },
    });
  };

  return (
    <>
      <AnswerQualityView
        headingRef={headingRef}
        status={status}
        weeks={weeks}
        offset={offset}
        reply={reply?.key === key ? reply.data : null}
        error={error?.key === key ? error.message : null}
        onChoose={choose}
        onRetry={() => setAttempt((n) => n + 1)}
        onHandle={handle}
      />
      {/* Focus goes to Undo: the card that had it is gone, and Undo lasts 8 seconds. */}
      <Toast
        message={toast?.text ?? null}
        onUndo={toast?.undo}
        focusUndo
        onClose={() => {
          queue.flush();
          setToast(null);
        }}
      />
    </>
  );
}

/** The page itself, from props only, so it renders in node (tests/admin-answer-quality.test.ts). */
export function AnswerQualityView({
  headingRef,
  status,
  weeks,
  offset,
  reply,
  error,
  onChoose,
  onRetry,
  onHandle,
}: {
  headingRef?: Ref<HTMLHeadingElement>;
  status: QualityStatus;
  weeks: QualityWeeks;
  offset: number;
  reply: AnswerQualityReply | null;
  error: string | null;
  onChoose(next: { status?: QualityStatus; weeks?: QualityWeeks; offset?: number }): void;
  onRetry(): void;
  onHandle(item: AnswerQualityItem): void;
}) {
  const pages = reply ? pageInfo(offset, reply.items.length, reply.total) : null;
  return (
    <section className={styles.section} aria-labelledby="quality-heading">
      <h2 id="quality-heading" ref={headingRef} tabIndex={-1} className={styles.sectionHeading}>
        {QUALITY_TEXT.heading}
      </h2>
      <p className="muted">{QUALITY_TEXT.intro}</p>
      <div className={quality.controls}>
        <Segmented<QualityStatus> label="Show" options={SHOW} value={status} onChange={(next) => onChoose({ status: next })} />
        <Segmented<QualityWeeks> label="Period" options={PERIOD} value={weeks} onChange={(next) => onChoose({ weeks: next })} />
      </div>
      {error ? (
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>{QUALITY_TEXT.failed}</p>
            <p>{error}</p>
          </div>
          <button type="button" className="btn" onClick={onRetry}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      ) : null}
      {!reply && !error ? (
        <div className={`delayed ${styles.skeletons}`}>
          <p className="muted" role="status">
            {QUALITY_TEXT.loading}
          </p>
          <Skeleton className={styles.skeletonRow} />
          <Skeleton className={styles.skeletonRow} />
        </div>
      ) : null}
      {reply ? <WeekTable reply={reply} /> : null}
      {reply && reply.items.length === 0 ? (
        <p className={styles.emptyLine}>{status === "open" ? QUALITY_TEXT.emptyOpen : QUALITY_TEXT.emptyHandled}</p>
      ) : null}
      {reply && reply.items.length > 0 ? (
        <ul className={quality.list} aria-label={status === "open" ? "Open answers" : "Handled answers"}>
          {reply.items.map((item) => (
            <li key={item.id}>
              <QualityCard item={item} status={status} onHandle={() => onHandle(item)} />
            </li>
          ))}
        </ul>
      ) : null}
      {pages && reply && reply.total > QUALITY_PAGE_SIZE ? (
        <nav className={styles.pager} aria-label="Answer quality pages">
          <span className="muted tabular">
            {pages.from}–{pages.to} of {formatNumber(reply.total)}
          </span>
          <button
            type="button"
            className="btn"
            disabled={!pages.hasPrevious}
            onClick={() => onChoose({ offset: Math.max(0, offset - QUALITY_PAGE_SIZE) })}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn"
            disabled={!pages.hasNext}
            onClick={() => onChoose({ offset: offset + QUALITY_PAGE_SIZE })}
          >
            Next
          </button>
        </nav>
      ) : null}
    </section>
  );
}

function WeekTable({ reply }: { reply: AnswerQualityReply }) {
  return (
    <table className={`table ${quality.weeks}`}>
      <caption className={quality.caption}>{QUALITY_TEXT.weeks}</caption>
      <thead>
        <tr>
          <th scope="col">Week of</th>
          <th scope="col">Useful</th>
          <th scope="col">Not useful</th>
        </tr>
      </thead>
      <tbody>
        {reply.weeks.map((week) => (
          <tr key={week.week}>
            <td>{formatDate(week.week)}</td>
            <td className="tabular">{formatNumber(week.useful)}</td>
            <td className="tabular">{formatNumber(week.not_useful)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function QualityCard({ item, status, onHandle }: { item: AnswerQualityItem; status: QualityStatus; onHandle(): void }) {
  const titleId = `quality-${item.id}`;
  return (
    <article className={quality.card} aria-labelledby={titleId}>
      <div className={quality.meta}>
        <h3 id={titleId} className={quality.who}>
          Asked by {item.asked_by}
        </h3>
        <p className={quality.metaLine}>Marked Not useful {formatDateTime(item.given_at)}</p>
        {item.handled_at ? <p className={quality.metaLine}>Handled {formatDateTime(item.handled_at)}</p> : null}
        <h4 className={quality.label}>What went wrong</h4>
        <p className={quality.text}>{reasonsText(item.reason_codes)}</p>
        {item.reason ? (
          <>
            <h4 className={quality.label}>In their words</h4>
            <p className={quality.text}>{item.reason}</p>
          </>
        ) : null}
        <div>
          <button type="button" className="btn" onClick={onHandle}>
            {status === "open" ? QUALITY_TEXT.handle : QUALITY_TEXT.reopen}
            <span className="visually-hidden">{`, asked by ${item.asked_by}`}</span>
          </button>
        </div>
      </div>
      <div className={quality.body}>
        <h4 className={quality.label}>Question</h4>
        <p className={quality.text}>{item.question ?? "—"}</p>
        <h4 className={quality.label}>Answer</h4>
        <AnswerView message={answerMessage(item)} question={item.question} readOnly />
      </div>
    </article>
  );
}
