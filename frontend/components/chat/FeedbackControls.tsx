"use client";

// "Was this helpful?" on one answer: Yes or No, with thumbs icons and the words.
// A No can carry a short reason; it is optional. The server keeps one rating
// per person per answer; the pressed button shows the saved one.

import { useState } from "react";
import { IconButton } from "@/components/ui/IconButton";
import { ApiError, sendFeedback } from "@/lib/api";
import { cleanReason, MAX_REASON_LENGTH } from "./answer";
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
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const reasonId = `reason-${messageId}`;

  const send = async (next: Rating, why?: string) => {
    setBusy(true);
    setError(null);
    try {
      await sendFeedback(messageId, next, why);
      setRating(next);
      setAskingReason(false);
      setStatus("Thank you. Your feedback was saved.");
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status !== 0
          ? "Could not save your feedback. Try again in a minute."
          : "Could not save your feedback. Check your internet connection, then try again.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.feedback} role="group" aria-label="Was this answer helpful?">
      <div className={styles.feedbackRow}>
        <span className={styles.feedbackQuestion}>Was this helpful?</span>
        <IconButton
          icon="thumbs-up"
          label="Yes"
          showLabel
          className={styles.vote}
          aria-pressed={rating === "up"}
          disabled={busy}
          onClick={() => void send("up")}
        />
        <IconButton
          icon="thumbs-down"
          label="No"
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
        <form
          id={reasonId}
          className={styles.reasonForm}
          onSubmit={(event) => {
            event.preventDefault();
            void send("down", cleanReason(reason));
          }}
        >
          <label className="label" htmlFor={`${reasonId}-text`}>
            What was wrong? (optional)
          </label>
          <textarea
            id={`${reasonId}-text`}
            className="input"
            rows={2}
            maxLength={MAX_REASON_LENGTH}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={busy}>
              Send feedback
            </button>
            <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setAskingReason(false)}>
              Cancel
            </button>
          </div>
        </form>
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
