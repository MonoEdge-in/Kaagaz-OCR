"use client";

// A password input with a Show/Hide control, used on sign-in and on the admin
// "New user" form. Typed characters are never changed: no trimming, no length
// cap, and the keyboard's spellcheck and autocorrect are off, so a visible
// password is not sent to a cloud dictionary. There is no name attribute, so
// the value can only leave the page through the app's own fetch call.
//
// Showing is controlled when `masked` is passed (sign-in masks it again on
// submit); otherwise the field keeps its own state. Either way it hides the
// password again when the page is left or put in the background.

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Icon } from "./Icon";
import {
  CAPS_LOCK_MESSAGE,
  capsLockFrom,
  caretToKeep,
  describedBy,
  inputType,
  passwordFieldIds,
  remaskOn,
  toggleText,
  visibilityMessage,
} from "./passwordFieldLogic";
import type { Caret } from "./passwordFieldLogic";
import styles from "./PasswordField.module.css";

export interface PasswordFieldProps {
  id: string;
  label: string;
  value: string;
  onChange(value: string): void;
  autoComplete: "current-password" | "new-password";
  error?: string | null;
  hint?: string;
  onBlur?(): void;
  required?: boolean;
  masked?: boolean;
  onMaskedChange?(masked: boolean): void;
  /** The mobile keyboard's Enter key; "done" unless the form goes on. */
  enterKeyHint?: "done" | "go" | "next";
}

export function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  error,
  hint,
  onBlur,
  required,
  masked: maskedProp,
  onMaskedChange,
  enterKeyHint = "done",
}: PasswordFieldProps) {
  const [ownMasked, setOwnMasked] = useState(true);
  const masked = maskedProp ?? ownMasked;
  const [capsLock, setCapsLock] = useState(false);
  // The live region stays empty until the person first toggles, so nothing is
  // announced on page load; after that it always matches what is on screen.
  const [toggled, setToggled] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const pendingCaret = useRef<Caret | null>(null);
  const ids = passwordFieldIds(id);

  const setMasked = useCallback(
    (next: boolean) => {
      if (maskedProp === undefined) setOwnMasked(next);
      onMaskedChange?.(next);
    },
    [maskedProp, onMaskedChange],
  );

  function toggle() {
    const input = inputRef.current;
    if (input) {
      pendingCaret.current = caretToKeep(
        input.selectionStart,
        input.selectionEnd,
        input.value.length,
        input.selectionDirection,
      );
    }
    setToggled(true);
    setMasked(!masked);
  }

  // Changing the type can move the caret; put the selection back where it was.
  useLayoutEffect(() => {
    const caret = pendingCaret.current;
    const input = inputRef.current;
    pendingCaret.current = null;
    if (!caret || !input || document.activeElement !== input) return;
    input.setSelectionRange(caret.start, caret.end, caret.direction);
  }, [masked]);

  // While the password is visible, hide it when the tab is left or hidden.
  useEffect(() => {
    if (masked) return;
    const hide = (event: Event) => {
      if (remaskOn(event.type, document.visibilityState)) setMasked(true);
    };
    window.addEventListener("pagehide", hide);
    document.addEventListener("visibilitychange", hide);
    return () => {
      window.removeEventListener("pagehide", hide);
      document.removeEventListener("visibilitychange", hide);
    };
  }, [masked, setMasked]);

  function readCapsLock(event: KeyboardEvent<HTMLInputElement>) {
    const on = capsLockFrom(event);
    if (on !== null) setCapsLock(on);
  }

  const hasError = Boolean(error);

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className={styles.control}>
        <input
          ref={inputRef}
          id={id}
          className={`input ${styles.input}`}
          type={inputType(masked)}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={readCapsLock}
          onKeyUp={readCapsLock}
          onBlur={() => {
            setCapsLock(false);
            onBlur?.();
          }}
          autoComplete={autoComplete}
          spellCheck={false}
          autoCapitalize="none"
          autoCorrect="off"
          enterKeyHint={enterKeyHint}
          required={required}
          aria-invalid={hasError ? true : undefined}
          aria-describedby={describedBy(ids, {
            error: hasError,
            capsLock,
            hint: Boolean(hint),
          })}
        />
        <button
          id={ids.toggle}
          type="button"
          className={styles.toggle}
          aria-controls={id}
          // Keep focus (and the caret) in the input when it is clicked or tapped.
          onMouseDown={(event) => {
            if (document.activeElement === inputRef.current) event.preventDefault();
          }}
          onClick={toggle}
        >
          {toggleText(masked)}
          <span className="visually-hidden"> password</span>
        </button>
      </div>
      <p id={ids.caps} className={styles.caps} aria-live="polite">
        {capsLock && (
          <>
            <Icon name="alert" size={16} />
            <span>{CAPS_LOCK_MESSAGE}</span>
          </>
        )}
      </p>
      {hasError && (
        <p id={ids.error} className={`error-text ${styles.message}`}>
          {error}
        </p>
      )}
      {hint && (
        <p id={ids.hint} className={`hint ${styles.message}`}>
          {hint}
        </p>
      )}
      <p id={ids.status} className="visually-hidden" aria-live="polite">
        {toggled ? visibilityMessage(masked) : ""}
      </p>
    </div>
  );
}
