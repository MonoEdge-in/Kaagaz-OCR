"use client";

// A clarifying question (PRD 6 B2): one short question and its choices, a group named by the
// question (checkboxes in a fieldset when several may be picked, with "All of these"). The
// choices are the company's own names, by server id. "Something else" puts the cursor in the
// composer and sends nothing. While a reply is on its way the choices are aria-disabled; once
// answered, the card is plain text ("You chose: Pune"). Text only: labels are document text.

import { useId, useState } from "react";
import type { Message } from "@/lib/types";
import { langOf } from "./clarify";
import styles from "./Chat.module.css";

export function ClarifyView({
  message,
  open,
  chosen,
  busy,
  onChoose,
  onOther,
}: {
  message: Message;
  /** The conversation's newest message, still to answer. */
  open: boolean;
  /** The labels picked, once answered (null: answered by typing). */
  chosen?: string | null;
  busy: boolean;
  onChoose: (message: Message, ids: string[]) => void;
  onOther: () => void;
}) {
  const id = useId();
  const [ticked, setTicked] = useState<string[]>([]);
  const clarify = message.answer?.clarify;
  if (!clarify) return null;
  const picks = clarify.choices.filter((choice) => choice.kind === "pick");
  const all = clarify.choices.find((choice) => choice.kind === "all");
  const other = clarify.choices.find((choice) => choice.kind === "other");
  const disabled = busy || undefined;
  const choose = (ids: string[]) => {
    if (!busy && ids.length > 0) onChoose(message, ids);
  };
  const question = (
    <p id={id} className={styles.plain}>
      {clarify.question}
    </p>
  );
  const label = (text: string) => (
    <span className={styles.chipText} lang={langOf(text)}>
      {text}
    </span>
  );
  const otherChip = other ? (
    <button type="button" className={styles.chip} aria-disabled={disabled} onClick={onOther}>
      {other.label}
    </button>
  ) : null;

  if (!open) {
    return (
      <div className={styles.answerCard}>
        {question}
        {chosen ? <p className={styles.understood}>You chose: {label(chosen)}</p> : null}
      </div>
    );
  }
  if (clarify.multi) {
    return (
      <div className={`motion-rise ${styles.answerCard}`}>
        <fieldset className={styles.choices} aria-disabled={disabled}>
          <legend className={styles.plain}>{clarify.question}</legend>
          {picks.map((choice) => (
            <label key={choice.id} className={styles.chip}>
              <input
                type="checkbox"
                checked={ticked.includes(choice.id)}
                onChange={(event) =>
                  setTicked((now) => (event.target.checked ? [...now, choice.id] : now.filter((x) => x !== choice.id)))
                }
              />
              {label(choice.label)}
            </label>
          ))}
          <button
            type="button"
            className={styles.chip}
            aria-disabled={busy || ticked.length === 0 || undefined}
            onClick={() => choose(ticked)}
          >
            Look in these
          </button>
          {all ? (
            <button type="button" className={styles.chip} aria-disabled={disabled} onClick={() => choose([all.id])}>
              {all.label}
            </button>
          ) : null}
          {otherChip}
        </fieldset>
      </div>
    );
  }
  return (
    <div className={`motion-rise ${styles.answerCard}`}>
      {question}
      <div role="group" aria-labelledby={id} className={styles.choices}>
        {picks.map((choice) => (
          <button
            key={choice.id}
            type="button"
            className={styles.chip}
            aria-disabled={disabled}
            onClick={() => choose([choice.id])}
          >
            {label(choice.label)}
            {choice.detail ? <span className={styles.chipDetail}>{choice.detail}</span> : null}
          </button>
        ))}
        {otherChip}
      </div>
    </div>
  );
}
