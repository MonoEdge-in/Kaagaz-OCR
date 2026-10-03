"use client";

// The composer, pinned to the foot of the thread. With a mouse or trackpad,
// Enter sends and Shift+Enter makes a new line; on a touch screen Enter makes a
// new line and the Send button sends. The box grows with the text up to about
// six lines. The box empties as soon as a question is sent, so the next one can
// be typed while the answer loads. If sending fails, the question comes back
// (unless something new was typed meanwhile) and the error offers "Try again".

import { useLayoutEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cleanQuestion, MAX_QUESTION_LENGTH, restoreAfterFailure, sendsOnKey } from "./answer";
import { useMediaQuery } from "./hooks";
import styles from "./Chat.module.css";

const INPUT_ID = "question";
const HINT_ID = "question-hint";
const EMPTY_ID = "question-empty";

/** Grow (or shrink) the box to its text; CSS caps it at about six lines. */
function fitToText(input: HTMLTextAreaElement) {
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight + input.offsetHeight - input.clientHeight}px`;
}

export function QuestionBox({
  pending,
  error,
  onAsk,
  inputRef,
}: {
  pending: boolean;
  /** Why the last question could not be sent, or null. */
  error: string | null;
  onAsk: (question: string) => Promise<boolean>;
  inputRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const [text, setText] = useState("");
  const [emptyTried, setEmptyTried] = useState(false);
  const coarse = useMediaQuery("(pointer: coarse)");
  // Set from the moment a question is accepted, so a quick second Enter or
  // click cannot send it twice before `pending` arrives.
  const sending = useRef(false);

  // Fit the box to its text after every change: typing, clearing on send, or
  // the question coming back after a failure. Empty, it goes back to one line.
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    if (text) fitToText(input);
    else input.style.height = "";
  }, [text, inputRef]);

  const submit = async () => {
    if (pending || sending.current) return;
    const question = cleanQuestion(text);
    if (!question) {
      setEmptyTried(true);
      inputRef.current?.focus();
      return;
    }
    const input = inputRef.current;
    const focused = document.activeElement;
    // Keep typing in the box when the send came from this form. On a touch
    // screen only when the box itself had focus: focusing it opens the keyboard.
    const keepFocus = coarse ? focused === input : Boolean(input?.form?.contains(focused));
    const sent = text;
    sending.current = true;
    setText("");
    if (keepFocus) input?.focus({ preventScroll: true });
    try {
      if (!(await onAsk(question))) setText((current) => restoreAfterFailure(current, sent));
    } finally {
      sending.current = false;
    }
  };

  const describedBy = [emptyTried ? EMPTY_ID : null, coarse ? null : HINT_ID]
    .filter(Boolean)
    .join(" ");

  return (
    <form
      className={styles.composerForm}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      {error ? (
        <div className={styles.sendError}>
          <p className={styles.sendErrorText} role="alert">
            <Icon name="alert" size={16} />
            <span>{error}</span>
          </p>
          <button type="button" className="btn" disabled={pending} onClick={() => void submit()}>
            <Icon name="refresh" size={16} />
            Try again
          </button>
        </div>
      ) : null}
      <label htmlFor={INPUT_ID} className="visually-hidden">
        Ask about your documents
      </label>
      <div className={styles.composerBox}>
        <textarea
          id={INPUT_ID}
          ref={inputRef}
          className={`input ${styles.composerInput}`}
          rows={1}
          maxLength={MAX_QUESTION_LENGTH}
          enterKeyHint={coarse ? "enter" : "send"}
          aria-invalid={emptyTried ? true : undefined}
          aria-describedby={describedBy || undefined}
          value={text}
          onChange={(event) => {
            setText(event.target.value);
            if (emptyTried) setEmptyTried(false);
          }}
          onKeyDown={(event) => {
            const { key, shiftKey } = event;
            if (sendsOnKey({ key, shiftKey, isComposing: event.nativeEvent.isComposing }, coarse)) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <IconButton
          icon="send"
          label="Send question"
          className={`btn-primary ${styles.send}`}
          // aria-disabled, not disabled: the button keeps focus while the answer
          // is out, and submit() ignores a press then.
          aria-disabled={pending || undefined}
          aria-busy={pending || undefined}
          onClick={() => void submit()}
        />
      </div>
      {emptyTried ? (
        <p id={EMPTY_ID} className="error-text">
          Type a question first.
        </p>
      ) : null}
      {coarse ? null : (
        <p id={HINT_ID} className={styles.composerNote}>
          Enter to send, Shift+Enter for a new line
        </p>
      )}
    </form>
  );
}
