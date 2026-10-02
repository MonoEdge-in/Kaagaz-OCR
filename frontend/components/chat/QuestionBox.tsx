"use client";

// The composer, pinned to the foot of the thread. With a mouse or trackpad,
// Enter sends and Shift+Enter makes a new line; on a touch screen Enter makes a
// new line and the Send button sends. The box grows with the text up to about
// six lines. The text stays in the box if sending fails, so nothing typed is
// lost, and the error offers "Try again".

import { useState } from "react";
import type { RefObject } from "react";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cleanQuestion, MAX_QUESTION_LENGTH, sendsOnKey } from "./answer";
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

  const submit = async () => {
    if (pending) return;
    const question = cleanQuestion(text);
    if (!question) {
      setEmptyTried(true);
      inputRef.current?.focus();
      return;
    }
    if (await onAsk(question)) {
      setText("");
      if (inputRef.current) inputRef.current.style.height = "";
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
            fitToText(event.target);
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
