"use client";

// Create organisation: its name and its first admin's e-mail and name. The
// reply carries the admin's temporary password ONCE: it is shown here with
// Copy and kept only in this dialog's memory (never stored, never logged), so
// it is gone when the dialog closes. The admin must change it at first sign-in.
// So the dialog cannot close while the request runs (the reply would be lost),
// and once the password shows only Done or Close shut it (not Esc or a click
// beside it).

import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { CopyButton } from "@/components/admin/CopyButton";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { ApiError, createOrg } from "@/lib/api";
import type { OrganisationCreated } from "@/lib/types";
import {
  TEMPORARY_PASSWORD_NOTE,
  canCloseDialog,
  firstInvalidOrgField,
  operatorErrorMessage,
  validateNewOrg,
} from "./logic";
import type { NewOrgErrors, NewOrgInput } from "./logic";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

const EMPTY: NewOrgInput = { name: "", admin_email: "", admin_name: "" };
const IDS: Record<keyof NewOrgInput, string> = {
  name: "new-org-name",
  admin_email: "new-org-admin-email",
  admin_name: "new-org-admin-name",
};

export function CreateOrgDialog({ onClose, onCreated }: { onClose(): void; onCreated(name: string): void }) {
  const [input, setInput] = useState<NewOrgInput>(EMPTY);
  const [errors, setErrors] = useState<NewOrgErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<OrganisationCreated | null>(null);
  const focusOnMount = useCallback((element: HTMLHeadingElement | null) => element?.focus(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFailure(null);
    const found = validateNewOrg(input);
    setErrors(found);
    const first = firstInvalidOrgField(found);
    if (first) {
      document.getElementById(IDS[first])?.focus();
      return;
    }
    setBusy(true);
    try {
      const reply = await createOrg({
        name: input.name.trim(),
        admin_email: input.admin_email.trim(),
        admin_name: input.admin_name.trim(),
      });
      setCreated(reply);
      onCreated(reply.organisation.name);
    } catch (caught) {
      const message = operatorErrorMessage(caught);
      if (caught instanceof ApiError && caught.status === 409) setErrors({ admin_email: message });
      else setFailure(message);
    } finally {
      setBusy(false);
    }
  }

  const field = (key: keyof NewOrgInput, label: string, type: "text" | "email") => (
    <Field
      id={IDS[key]}
      label={label}
      type={type}
      value={input[key]}
      error={errors[key]}
      autofocus={key === "name"}
      onChange={(value) => setInput((was) => ({ ...was, [key]: value }))}
    />
  );

  return (
    <ActionDialog
      title="Create organisation"
      onClose={onClose}
      canClose={(way) => canCloseDialog({ busy, secretShown: created !== null }, way)}
    >
      {created ? (
        <section className={styles.dialogForm} aria-labelledby="org-created-heading">
          <h3 id="org-created-heading" ref={focusOnMount} tabIndex={-1} className={styles.resultHeading}>
            {created.organisation.name} is ready
          </h3>
          <p>
            Temporary password for {created.organisation.admin.name} ({created.organisation.admin.email}). It is
            shown only this once.
          </p>
          <div className={admin.secretRow}>
            <code className={admin.secret} translate="no">
              {created.temporary_password}
            </code>
            <CopyButton text={created.temporary_password} what="Password" buttonText="Copy password" />
          </div>
          <p>{TEMPORARY_PASSWORD_NOTE}</p>
          <div className={styles.buttons}>
            <button type="button" className="btn btn-primary" onClick={onClose}>
              Done
            </button>
          </div>
        </section>
      ) : (
        <form className={styles.dialogForm} onSubmit={submit} noValidate>
          {field("name", "Organisation name", "text")}
          {field("admin_email", "First admin's e-mail", "email")}
          {field("admin_name", "First admin's name", "text")}
          <p className="muted">They get a temporary password, shown to you once.</p>
          {failure && (
            <p className="error-text" role="alert">
              {failure}
            </p>
          )}
          <div className={styles.buttons}>
            <button type="button" className="btn" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
              {busy && <span className="spinner" aria-hidden="true" />}
              {busy ? "Creating…" : "Create organisation"}
            </button>
          </div>
        </form>
      )}
    </ActionDialog>
  );
}

function Field(props: {
  id: string;
  label: string;
  type: "text" | "email";
  value: string;
  error: string | undefined;
  autofocus: boolean;
  onChange(value: string): void;
}) {
  const { id, label, type, value, error, autofocus, onChange } = props;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        type={type}
        autoComplete="off"
        spellCheck={false}
        autoCapitalize={type === "email" ? "none" : "words"}
        inputMode={type === "email" ? "email" : undefined}
        data-autofocus={autofocus || undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
      />
      {error && (
        <span id={`${id}-error`} className="error-text">
          {error}
        </span>
      )}
    </div>
  );
}
