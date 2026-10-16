"use client";

// Pieces the My words screen and the upload's "Words in these files" share:
// the two fields (word, meaning) with the instant text check, and the question
// asked when a word already has another meaning (409 word_has_meaning). The
// dialog is ActionDialog (a native modal <dialog>: focus stays inside, Esc and
// the close button are Cancel); focus starts on Cancel, the safe choice.

import { ActionDialog } from "@/components/documents/ActionDialog";
import { duplicateQuestion, MEANING_MAX, textProblem, WORD_MAX } from "./words";
import type { Choice, Existing, WordDraft } from "./words";
import styles from "./Words.module.css";

interface WordFieldsProps {
  idPrefix: string;
  draft: WordDraft;
  onChange(draft: WordDraft): void;
  checked: boolean; // check an empty field too (after a first try); typed text is always checked
  labelSuffix?: string; // for screen readers, when there are several pairs
}

export function WordFields({ idPrefix, draft, onChange, checked, labelSuffix }: WordFieldsProps) {
  const field = (what: "word" | "meaning", label: string, max: number) => {
    const id = `${idPrefix}-${what}`;
    const problem = checked || draft[what] !== "" ? textProblem(draft[what], what) : null;
    return (
      <div className={`field ${styles.field}`}>
        <label htmlFor={id}>
          {label}
          {labelSuffix && <span className="visually-hidden">{labelSuffix}</span>}
        </label>
        <input
          id={id}
          className="input"
          value={draft[what]}
          maxLength={max * 2}
          autoComplete="off"
          spellCheck={false}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-problem` : undefined}
          onChange={(event) => onChange({ ...draft, [what]: event.target.value })}
        />
        {problem && <p id={`${id}-problem`} className="error-text">{problem}</p>}
      </div>
    );
  };
  return (
    <>
      {field("word", "Word, as you type it", WORD_MAX)}
      {field("meaning", "What it means, in English", MEANING_MAX)}
    </>
  );
}

export function DuplicateDialog(props: { draft: WordDraft; existing: Existing[]; onChoose(choice: Choice): void }) {
  const { draft, existing, onChoose } = props;
  const cancel = () => onChoose({ kind: "cancel" });
  return (
    <ActionDialog title="This word already has a meaning" onClose={cancel}>
      <p>{duplicateQuestion(draft.word, draft.meaning, existing)}</p>
      {/* [Replace ...] (it takes the old meaning away) ... [Cancel] [Add too]. */}
      <div className={styles.dialogButtons}>
        {existing.map((row) => (
          <button key={row.id} type="button" className={`btn ${styles.target} ${styles.destructive}`} onClick={() => onChoose({ kind: "replace", id: row.id })}>
            Replace &apos;{row.meaning}&apos;
          </button>
        ))}
        <button type="button" className={`btn ${styles.target}`} data-autofocus onClick={cancel}>Cancel</button>
        <button type="button" className={`btn btn-primary ${styles.target}`} onClick={() => onChoose({ kind: "add_too" })}>Add too</button>
      </div>
    </ActionDialog>
  );
}
