// The 6-digit code from an authenticator app. The phone shows its number
// keyboard (inputMode numeric) and can fill a code it received
// (one-time-code). Typed characters are kept as typed and checked on submit,
// so a Devanagari or fullwidth digit gets a clear message instead of silently
// vanishing. There is no name attribute, like every sign-in field.

import styles from "./twoStep.module.css";

export function CodeField({
  id,
  label,
  value,
  onChange,
  onBlur,
  error,
  hint,
  enterKeyHint = "go",
  autofocus = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange(value: string): void;
  onBlur?(): void;
  error: string | null;
  hint?: string;
  enterKeyHint?: "go" | "done";
  /** Focused when an ActionDialog opens (its data-autofocus). */
  autofocus?: boolean;
}) {
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const describedBy = [error ? errorId : "", hint ? hintId : ""]
    .filter((part) => part !== "")
    .join(" ");
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className={`input ${styles.code}`}
        type="text"
        inputMode="numeric"
        autoComplete="one-time-code"
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        enterKeyHint={enterKeyHint}
        data-autofocus={autofocus || undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onBlur={onBlur}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
      />
      {error && (
        <p id={errorId} className={`error-text ${styles.message}`}>
          {error}
        </p>
      )}
      {hint && (
        <p id={hintId} className={`hint ${styles.message}`}>
          {hint}
        </p>
      )}
    </div>
  );
}
