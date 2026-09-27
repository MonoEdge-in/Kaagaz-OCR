"use client";

// Thumbs up or down on one answer. A down vote can carry a short reason; it is
// optional. The server keeps one rating per person per answer.

import { useState } from "react";
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
      setError(caught instanceof ApiError ? caught.message : "Could not save your feedback.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack" role="group" aria-label="Was this answer helpful?">
      <div className="row">
        <span className="muted">Was this helpful?</span>
        <button
          type="button"
          className="btn"
          aria-pressed={rating === "up"}
          disabled={busy}
          onClick={() => send("up")}
        >
          <span aria-hidden="true">&#128077;</span> Yes
        </button>
        <button
          type="button"
          className="btn"
          aria-pressed={rating === "down"}
          aria-expanded={askingReason}
          aria-controls={askingReason ? reasonId : undefined}
          disabled={busy}
          onClick={() => setAskingReason(true)}
        >
          <span aria-hidden="true">&#128078;</span> No
        </button>
        {rating ? (
          <span className="muted">You said: {rating === "up" ? "helpful" : "not helpful"}</span>
        ) : null}
      </div>
      {askingReason ? (
        <form
          id={reasonId}
          className={`stack ${styles.reasonForm}`}
          onSubmit={(event) => {
            event.preventDefault();
            void send("down", cleanReason(reason));
          }}
        >
          <label htmlFor={`${reasonId}-text`}>What was wrong? (optional)</label>
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
              Send
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => setAskingReason(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {error ? <p className="error-text">{error}</p> : null}
      <p className="visually-hidden" aria-live="polite">
        {status}
      </p>
    </div>
  );
}
