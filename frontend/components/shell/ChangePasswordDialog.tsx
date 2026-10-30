"use client";

// Change password, from the account menu, for every role (#457 phase 0): the current
// password, the new one twice, and the 6-digit code for anyone with two-step sign-in on.
// The server signs every other browser out, keeps this one and emails a security notice.
// Like the sign-in form: method="post", no name attributes, passwords masked again on
// submit and never trimmed. The rules and words are setPassword.ts's, as the
// set-new-password screen's; the code's are the sign-in's (app/login/twoStep.ts).

import { useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { CodeField } from "@/app/login/CodeField";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import { MIN_PASSWORD_LENGTH } from "@/components/admin/logic";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { PasswordField } from "@/components/ui/PasswordField";
import { changePassword } from "@/lib/api";
import type { Me } from "@/lib/types";
import { firstInvalidNewPassword, setPasswordError, validateNewPassword } from "./setPassword";
import type { NewPasswordErrors, NewPasswordField, NewPasswordInput, SetPasswordError } from "./setPassword";

const IDS: Record<NewPasswordField | "code", string> = {
  current: "change-password-current",
  next: "change-password-new",
  again: "change-password-again",
  code: "change-password-code",
};
const ALL_MASKED: Record<NewPasswordField, boolean> = { current: true, next: true, again: true };
export const PASSWORD_CHANGED = "Your password is changed. Your other devices are signed out.";

export function ChangePasswordDialog({ me, onClose, onDone }: { me: Me; onClose(): void; onDone(message: string): void }) {
  const withCode = me.user.totp_enabled;
  const [input, setInput] = useState<NewPasswordInput>({ current: "", next: "", again: "" });
  const [code, setCode] = useState("");
  const [errors, setErrors] = useState<NewPasswordErrors>({});
  const [codeProblem, setCodeProblem] = useState<string | null>(null);
  const [masked, setMasked] = useState(ALL_MASKED);
  const [failure, setFailure] = useState<SetPasswordError | null>(null);
  const [busy, setBusy] = useState(false);

  function change(field: NewPasswordField, value: string) {
    const next = { ...input, [field]: value };
    setInput(next);
    if (errors[field]) setErrors((was) => ({ ...was, [field]: validateNewPassword(next)[field] }));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(ALL_MASKED);
    setFailure(null);
    const found = validateNewPassword(input);
    const problem = withCode ? codeError(code) : null;
    const first = firstInvalidNewPassword(found) ?? (problem ? "code" : null);
    if (first) {
      flushSync(() => {
        setErrors(found);
        setCodeProblem(problem);
      });
      document.getElementById(IDS[first])?.focus();
      return;
    }
    setErrors(found);
    setCodeProblem(null);
    setBusy(true);
    try {
      await changePassword(input.current, input.next, withCode ? normalizeCode(code) : undefined);
      onDone(PASSWORD_CHANGED);
    } catch (caught) {
      setFailure(setPasswordError(caught));
      setCode(""); // a code works once
      setBusy(false);
    }
  }

  const field = (id: NewPasswordField) => ({
    id: IDS[id],
    value: input[id],
    onChange: (value: string) => change(id, value),
    error: errors[id] ?? null,
    masked: masked[id],
    onMaskedChange: (value: boolean) => setMasked((was) => ({ ...was, [id]: value })),
  });

  return (
    <ActionDialog title="Change password" onClose={onClose} canClose={() => !busy}>
      <form method="post" noValidate onSubmit={submit}>
        <PasswordField {...field("current")} label="Current password" autoComplete="current-password" required />
        <PasswordField
          {...field("next")}
          label="New password"
          autoComplete="new-password"
          hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
          required
        />
        <PasswordField {...field("again")} label="New password again" autoComplete="new-password" required />
        {withCode && (
          <CodeField
            id={IDS.code}
            label="6-digit code"
            value={code}
            onChange={(value) => {
              setCode(value);
              if (codeProblem) setCodeProblem(codeError(value));
            }}
            error={codeProblem}
            hint="From your authenticator app."
            enterKeyHint="done"
          />
        )}
        {failure && (
          <p className="error-text" role="alert">
            {failure.message}
          </p>
        )}
        <div className="actions">
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {busy ? "Saving…" : "Change password"}
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
