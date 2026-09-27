"use client";

// The question box. Enter sends, Shift+Enter makes a new line. The text stays
// in the box if sending fails, so nothing typed is lost.

import { useState } from "react";
import { cleanQuestion, MAX_QUESTION_LENGTH, sendsOnKey } from "./answer";
import styles from "./Chat.module.css";

export function QuestionBox({
  pending,
  onAsk,
}: {
  pending: boolean;
  onAsk: (question: string) => Promise<boolean>;
}) {
  const [text, setText] = useState("");

  const submit = async () => {
    const question = cleanQuestion(text);
    if (!question || pending) return;
    if (await onAsk(question)) setText("");
  };

  return (
    <form
      className={`stack ${styles.questionBox}`}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <label htmlFor="question">Ask about your documents</label>
      <textarea
        id="question"
        className="input"
        rows={3}
        maxLength={MAX_QUESTION_LENGTH}
        placeholder="For example: Kiska profit sabse jyada tha last month?"
        aria-describedby="question-hint"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          const { key, shiftKey } = event;
          if (sendsOnKey({ key, shiftKey, isComposing: event.nativeEvent.isComposing })) {
            event.preventDefault();
            void submit();
          }
        }}
      />
      <div className="row">
        <button type="submit" className="btn btn-primary" disabled={pending || !cleanQuestion(text)}>
          {pending ? "Asking…" : "Ask"}
        </button>
        <span id="question-hint" className="muted">
          Enter sends. Shift+Enter starts a new line.
        </span>
      </div>
    </form>
  );
}
