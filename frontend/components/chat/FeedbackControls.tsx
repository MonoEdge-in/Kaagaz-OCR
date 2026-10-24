"use client";

// Useful or Not useful on one answer (PRD 6 F1), with thumbs icons and the words. Not
// useful opens a short form: what went wrong (boxes, any number), up to 500 characters with
// a visible count, and above Send the notice that the company admin sees it
// (FEEDBACK_NOTICE). The server keeps one rating per person per answer; the pressed button
// shows the saved one. The stored ratings stay "up" and "down".

import { useState } from "react";
import { IconButton } from "@/components/ui/IconButton";
import { ApiError, sendFeedback } from "@/lib/api";
import type { ReasonCode } from "@/lib/types";
import { cleanReason, FEEDBACK_NOTICE, MAX_REASON_LENGTH, REASONS } from "./answer";
import styles from "./Chat.module.css";

type Rating = "up" | "down";

export function FeedbackControls({
  messageId,
  initial,
}: {
  messageId: string;
  initial: Rating | null;
}) {
  const [rating, setRating] = useState<Rating | null>(initial);
  const [askingReason, setAskingReason] = useState(false);
  const [codes, setCodes] = useState<ReasonCode[]>([]);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const reasonId = `reason-${messageId}`;

  const send = async (next: Rating, details?: { reason?: string; codes: ReasonCode[] }) => {
    setBusy(true);
    setError(null);
    try {
      await sendFeedback(messageId, next, details);
      setRating(next);
      setAskingReason(false);
      setStatus("Thank you. Your feedback was saved.");
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 429
          ? caught.message
          : caught instanceof ApiError && caught.status !== 0
            ? "Could not save your feedback. Try again in a minute."
            : "Could not save your feedback. Check your internet connection, then try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.feedback} role="group" aria-label="Was this answer useful?">
      <div className={styles.feedbackRow}>
        <IconButton
          icon="thumbs-up"
          label="Useful"
          showLabel
          className={styles.vote}
          aria-pressed={rating === "up"}
          disabled={busy}
          onClick={() => void send("up")}
        />
        <IconButton
          icon="thumbs-down"
          label="Not useful"
          showLabel
          className={styles.vote}
          aria-pressed={rating === "down"}
          aria-expanded={askingReason}
          aria-controls={askingReason ? reasonId : undefined}
          disabled={busy}
          onClick={() => setAskingReason(true)}
        />
      </div>
      {askingReason ? (
        <ReasonForm
          id={reasonId}
          codes={codes}
          reason={reason}
          busy={busy}
          onCodes={setCodes}
          onReason={setReason}
          onCancel={() => setAskingReason(false)}
          onSend={() => void send("down", { reason: cleanReason(reason), codes })}
        />
      ) : null}
      {error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : null}
      <p className="visually-hidden" aria-live="polite">
        {status}
      </p>
    </div>
  );
}

/** The Not useful form: the reasons, the text with its count, the notice, then Cancel and Send. */
export function ReasonForm({
  id,
  codes,
  reason,
  busy,
  onCodes,
  onReason,
  onCancel,
  onSend,
}: {
  id: string;
  codes: ReasonCode[];
  reason: string;
  busy: boolean;
  onCodes(codes: ReasonCode[]): void;
  onReason(reason: string): void;
  onCancel(): void;
  onSend(): void;
}) {
  return (
    <form
      id={id}
      className={styles.reasonForm}
      onSubmit={(event) => {
        event.preventDefault();
        onSend();
      }}
    >
      <fieldset className={styles.reasonSet}>
        <legend className="label">What went wrong?</legend>
        <div className={styles.reasonChips}>
          {REASONS.map((choice) => (
            <label key={choice.code} className={styles.reasonChip}>
              <input
                type="checkbox"
                checked={codes.includes(choice.code)}
                onChange={(event) =>
                  onCodes(
                    REASONS.map((each) => each.code).filter((code) =>
                      code === choice.code ? event.target.checked : codes.includes(code),
                    ),
                  )
                }
              />
              <span>{choice.label}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="label" htmlFor={`${id}-text`}>
        Tell us more (optional)
      </label>
      <textarea
        id={`${id}-text`}
        className="input"
        rows={2}
        maxLength={MAX_REASON_LENGTH}
        aria-describedby={`${id}-count`}
        value={reason}
        onChange={(event) => onReason(event.target.value)}
      />
      <p id={`${id}-count`} className={`${styles.reasonCount} tabular`}>
        {reason.length} of {MAX_REASON_LENGTH} characters
      </p>
      <p className={styles.reasonNotice}>{FEEDBACK_NOTICE}</p>
      {/* Cancel, then the form's own action last; secondary, as Send in the
          question box is the screen's one primary. */}
      <div className="actions">
        <button type="button" className="btn btn-quiet" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className="btn" disabled={busy}>
          Send feedback
        </button>
      </div>
    </form>
  );
}
