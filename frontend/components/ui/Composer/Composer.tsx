// Where a question is typed (Ask): the question box on top, a toolbar inside
// the card with the screen's chips on the left and Send on the right. Enter
// sends, Shift+Enter starts a new line. SuggestionChip is a question to ask
// with one press. DevanagariKeys is the letter helper that opens under the
// composer for typing Marathi and Hindi words without a Devanagari keyboard.

import type { KeyboardEvent, ReactNode, RefObject } from "react";
import { Button } from "../Button/Button";
import styles from "./Composer.module.css";

export interface ComposerProps {
  id: string;
  /** Names the box for screen readers: "Your question". */
  label: string;
  value: string;
  onChange(value: string): void;
  onSend(): void;
  placeholder?: string;
  /** Asking now: Send shows it and ignores presses. */
  pending?: boolean;
  /** The screen's chips: scope, mode, the keys helper. */
  tools?: ReactNode;
  inputRef?: RefObject<HTMLTextAreaElement | null>;
  maxLength?: number;
}

export function Composer({ id, label, value, onChange, onSend, placeholder, pending, tools, inputRef, maxLength }: ComposerProps) {
  const empty = value.trim() === "";
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!empty && !pending) onSend();
  };
  return (
    <div className={styles.composer}>
      <textarea
        ref={inputRef}
        id={id}
        aria-label={label}
        rows={2}
        value={value}
        placeholder={placeholder}
        maxLength={maxLength}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
      />
      <div className={styles.toolbar}>
        <div className={styles.tools}>{tools}</div>
        <Button variant="primary" icon="send" loading={pending} disabled={empty && !pending} onClick={onSend}>
          {pending ? "Asking…" : "Send"}
        </Button>
      </div>
    </div>
  );
}

export function SuggestionChip({ children, onAsk }: { children: string; onAsk(question: string): void }) {
  return (
    <button type="button" className={styles.suggest} onClick={() => onAsk(children)}>
      {children}
    </button>
  );
}

/** The letters on the helper, in the handoff's order: vowels, consonants, then the signs. */
export const DEVANAGARI_KEYS = [
  "अ", "आ", "इ", "ई", "उ", "ऊ", "ए", "ऐ", "ओ", "औ", "ं", "्",
  "क", "ख", "ग", "घ", "च", "ज", "ट", "ड", "त", "द", "न", "प",
  "ब", "म", "य", "र", "ल", "व", "श", "स", "ह", "ा", "ि", "ी",
  "ु", "ू", "े", "ै", "ो", "ौ", "ृ",
] as const;

export interface DevanagariKeysProps {
  onKey(letter: string): void;
  onSpace(): void;
  onErase(): void;
}

export function DevanagariKeys({ onKey, onSpace, onErase }: DevanagariKeysProps) {
  return (
    <div className={styles.keys} role="group" aria-label="Devanagari letters">
      {DEVANAGARI_KEYS.map((letter) => (
        <button key={letter} type="button" lang="mr" onClick={() => onKey(letter)}>
          {letter}
        </button>
      ))}
      <button type="button" className={styles.space} onClick={onSpace}>
        Space
      </button>
      <button type="button" className={styles.erase} onClick={onErase}>
        Delete
      </button>
    </div>
  );
}
