"use client";

// Create organisation (its name and its first admin's e-mail and name), or,
// given an organisation, one more admin of it (e-mail, name and a fresh code:
// the temporary password signs in as that organisation's admin, so it is a
// step-up). The reply carries the admin's temporary password ONCE: it is shown
// here with Copy and kept only in this dialog's memory (never stored, never
// logged), so it is gone when the dialog closes. The admin must change it at
// first sign-in.
// So it cannot close while the request runs, and a shown password closes only
// by Done or Close (canCloseDialog).

import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { CodeField } from "@/app/admin/login/CodeField";
import { normalizeCode } from "@/app/admin/login/adminSignIn";
import { CopyButton } from "@/components/admin/CopyButton";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { createAdmin, createOrg } from "@/lib/api";
import type { Organisation } from "@/lib/types";
import {
  TEMPORARY_PASSWORD_NOTE,
  canCloseDialog,
  firstInvalidOrgField,
  isTakenEmail,
  operatorErrorMessage,
  validateNewOrg,
} from "./logic";
import type { NewOrgErrors, NewOrgInput } from "./logic";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

const EMPTY: NewOrgInput = { name: "", admin_email: "", admin_name: "", code: "" };
const IDS: Record<keyof NewOrgInput, string> = {
  name: "new-org-name",
  admin_email: "new-org-admin-email",
  admin_name: "new-org-admin-name",
  code: "new-admin-code",
};

/** What the reply gave, while the dialog is open. */
interface Created {
  heading: string;
  who: string;
  password: string;
}

export function CreateDialog({
  org,
  onClose,
  onCreated,
}: {
  /** Given: one more admin of this organisation. */
  org?: Organisation;
  onClose(): void;
  onCreated(message: string): void;
}) {
  const [input, setInput] = useState<NewOrgInput>(EMPTY);
  const [errors, setErrors] = useState<NewOrgErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<Created | null>(null);
  const focusOnMount = useCallback((element: HTMLHeadingElement | null) => element?.focus(), []);
  const action = org ? "Create admin" : "Create organisation";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFailure(null);
    const found = validateNewOrg(input, org !== undefined);
    setErrors(found);
    const first = firstInvalidOrgField(found);
    if (first) {
      document.getElementById(IDS[first])?.focus();
      return;
    }
    setBusy(true);
    const email = input.admin_email.trim();
    const name = input.admin_name.trim();
    try {
      if (org) {
        const reply = await createAdmin(org.id, { email, name, code: normalizeCode(input.code ?? "") });
        const who = `${reply.admin.name} (${reply.admin.email})`;
        setCreated({ heading: `${reply.admin.name} is an admin now`, who, password: reply.temporary_password });
        onCreated(`Created admin ${reply.admin.name}.`);
      } else {
        const reply = await createOrg({ name: input.name.trim(), admin_email: email, admin_name: name });
        const who = `${reply.organisation.admin.name} (${reply.organisation.admin.email})`;
        setCreated({ heading: `${reply.organisation.name} is ready`, who, password: reply.temporary_password });
        onCreated(`Created ${reply.organisation.name}.`);
      }
    } catch (caught) {
      const message = operatorErrorMessage(caught);
      if (isTakenEmail(caught)) setErrors({ admin_email: message });
      else setFailure(message);
    } finally {
      setInput((was) => ({ ...was, code: "" })); // a code works once: never kept
      setBusy(false);
    }
  }

  const field = (key: "name" | "admin_email" | "admin_name", label: string, type: "text" | "email") => (
    <Field
      id={IDS[key]}
      label={label}
      type={type}
      value={input[key]}
      error={errors[key]}
      autofocus={key === (org ? "admin_email" : "name")}
      onChange={(value) => setInput((was) => ({ ...was, [key]: value }))}
    />
  );

  return (
    <ActionDialog
      title={org ? `New admin of ${org.name}` : "Create organisation"}
      onClose={onClose}
      canClose={(way) => canCloseDialog({ busy, secretShown: created !== null }, way)}
    >
      {created ? (
        <section className={styles.dialogForm} aria-labelledby="created-heading">
          <h3 id="created-heading" ref={focusOnMount} tabIndex={-1} className={styles.resultHeading}>
            {created.heading}
          </h3>
          <p>Temporary password for {created.who}. It is shown only this once.</p>
          <div className={admin.secretRow}>
            <code className={admin.secret} translate="no">
              {created.password}
            </code>
            <CopyButton text={created.password} what="Password" buttonText="Copy password" />
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
          {!org && field("name", "Organisation name", "text")}
          {field("admin_email", org ? "Admin's e-mail" : "First admin's e-mail", "email")}
          {field("admin_name", org ? "Admin's name" : "First admin's name", "text")}
          {org && (
            <CodeField
              id={IDS.code}
              label="A new 6-digit code from your app"
              value={input.code ?? ""}
              onChange={(code) => setInput((was) => ({ ...was, code }))}
              error={errors.code ?? null}
              hint="Wait for a new code: the one you signed in with does not work again."
            />
          )}
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
            <button
              type="submit"
              className={`btn btn-primary ${busy ? admin.busy : ""}`}
              disabled={busy}
              aria-busy={busy || undefined}
            >
              {busy && <span className="spinner" aria-hidden="true" />}
              {action}
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
