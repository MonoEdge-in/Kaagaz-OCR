// A labelled field that tells you how to fix it (Components.html, 07): the
// label, the control, a help line and an error line with an icon and words, so
// red is never the only signal. Field hands the control its id and the ids of
// the lines that describe it, so a screen reader reads them with the control:
//
//   <Field id="gstin" label="GSTIN" error={error}>
//     {(control) => <TextInput {...control} mono value={gstin} onChange={…} />}
//   </Field>

import type { InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { cx } from "../cx";
import { Icon } from "../Icon/Icon";
import type { KIconName } from "../Icon/Icon";
import styles from "./Field.module.css";

export interface ControlProps {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
}

export interface FieldProps {
  id: string;
  label: ReactNode;
  help?: ReactNode;
  /** What is wrong and how to fix it: "Enter a 15-character GSTIN, like 27AABCM1234F1Z5". */
  error?: ReactNode;
  className?: string;
  children: (control: ControlProps) => ReactNode;
}

export function Field({ id, label, help, error, className, children }: FieldProps) {
  const helpId = help ? `${id}-help` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [errorId, helpId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cx(styles.field, className)}>
      <label className={styles.label} htmlFor={id}>
        {label}
      </label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {error ? (
        <span className={styles.error} id={errorId}>
          <Icon name="alert" size="sm" />
          {error}
        </span>
      ) : null}
      {help ? (
        <span className={styles.help} id={helpId}>
          {help}
        </span>
      ) : null}
    </div>
  );
}

export interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  /** An icon at the start: search, calendar. */
  icon?: KIconName;
  /** Values, ids and numbers: GSTIN, survey numbers. */
  mono?: boolean;
}

export function TextInput({ icon, mono, className, ...rest }: TextInputProps) {
  const input = <input {...rest} className={cx(styles.input, mono && styles.mono, className)} />;
  if (!icon) return input;
  return (
    <span className={styles.inputWrap}>
      <Icon name={icon} />
      {input}
    </span>
  );
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <span className={styles.selectWrap}>
      <select {...rest} className={cx(styles.select, className)}>
        {children}
      </select>
      <Icon name="chev-down" />
    </span>
  );
}

export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={cx(styles.textarea, className)} />;
}
