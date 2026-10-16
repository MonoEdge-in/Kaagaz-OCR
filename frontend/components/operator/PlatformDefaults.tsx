"use client";

// Platform defaults, in the Models tab (features 25-27, council 15 Oct 2026, C10, C12): what
// every organisation that has not been given its own settings uses, per area (scanning:
// models, default, thinking level, Dynamic; answering: models, default, levels people may
// choose, default level), with how many organisations follow each area now. "Change
// platform defaults" opens the same fields as an organisation's (AiFields), without
// Ensemble and budget; before saving it says which organisations the change reaches, and
// it asks for a fresh code (step-up). A refusal is said next to the field it is about. The
// focus goes back to the button when the dialog closes.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { CodeField } from "@/app/admin/login/CodeField";
import { codeError, normalizeCode } from "@/app/admin/login/adminSignIn";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { getAiDefaults, putAiDefaults } from "@/lib/api";
import type { AiDefaults, OperatorModel } from "@/lib/types";
import { LoadFailed } from "./LoadFailed";
import { canCloseDialog, countText, operatorErrorMessage } from "./logic";
import { aiBody, aiErrors, defaultsImpact, draftOf, inheritedText } from "./models";
import type { AiDraft, AiErrors } from "./models";
import { AiFields, CODE_ID, fieldId, firstProblem, refusalOf } from "./OrgAiSettings";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

const CHANGE_ID = "platform-defaults-change";

export function PlatformDefaults({ catalogue, onSaved }: { catalogue: OperatorModel[]; onSaved(message: string): void }) {
  const [attempt, setAttempt] = useState(0);
  const [reply, setReply] = useState<AiDefaults | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAiDefaults().then(
      (result) => !cancelled && setReply(result),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const failed = error?.attempt === attempt ? error.message : null;
  return (
    <section className={admin.section} aria-labelledby="platform-defaults-heading">
      <h2 id="platform-defaults-heading" className={admin.sectionHeading}>
        Platform defaults
      </h2>
      <p className={styles.summary}>
        Every organisation uses these until you give it its own settings (Organisations, AI settings), one area at a
        time.
      </p>
      {failed && !reply && <LoadFailed what="the platform defaults" message={failed} onRetry={() => setAttempt((n) => n + 1)} />}
      {reply && (
        <>
          <DefaultsSummary reply={reply} catalogue={catalogue} />
          <div>
            <button id={CHANGE_ID} type="button" className="btn" onClick={() => setEditing(true)}>
              Change platform defaults
            </button>
          </div>
        </>
      )}
      {reply && editing && (
        <DefaultsDialog
          reply={reply}
          catalogue={catalogue}
          onClose={() => setEditing(false)}
          onSaved={(next) => {
            setReply(next);
            onSaved("Saved the platform defaults.");
          }}
        />
      )}
    </section>
  );
}

/** The defaults in words, each with how many organisations follow it now. */
export function DefaultsSummary({ reply, catalogue }: { reply: AiDefaults; catalogue: OperatorModel[] }) {
  const { defaults, env, orgs_inheriting: counts } = reply;
  const rows: { label: string; value: string; count: number }[] = [
    { label: "Scanning models", value: inheritedText("scan_models", defaults, env, catalogue), count: counts.scan_models },
    { label: "Scanning thinking level", value: inheritedText("scan_thinking", defaults, env, catalogue), count: counts.scan_thinking },
    { label: "Dynamic", value: inheritedText("dynamic", defaults, env, catalogue), count: counts.dynamic },
    { label: "Answering models", value: inheritedText("chat_models", defaults, env, catalogue), count: counts.chat_models },
    { label: "Answering levels", value: inheritedText("chat_levels", defaults, env, catalogue), count: counts.chat_levels },
  ];
  return (
    <dl className="stack">
      {rows.map((row) => (
        <div key={row.label}>
          <dt className="label">{row.label}</dt>
          <dd>
            {row.value}
            <span className={styles.statusNote}>Followed by {countText(row.count, "organisation", "organisations")}</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}

function DefaultsDialog(props: { reply: AiDefaults; catalogue: OperatorModel[]; onClose(): void; onSaved(reply: AiDefaults): void }) {
  const { reply, catalogue, onClose, onSaved } = props;
  const [draft, setDraft] = useState<AiDraft>(() => draftOf(reply.defaults));
  const [errors, setErrors] = useState<AiErrors>({});
  const [code, setCode] = useState("");
  const [codeProblem, setCodeProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const found = aiErrors(draft, true);
    const badCode = codeError(code);
    setErrors(found);
    setCodeProblem(badCode);
    const first = firstProblem(found);
    if (first || badCode) {
      document.getElementById(first ? fieldId(first) : CODE_ID)?.focus();
      return;
    }
    setBusy(true);
    try {
      onSaved(await putAiDefaults(aiBody(draft), normalizeCode(code)));
      onClose();
    } catch (caught) {
      const refusal = refusalOf(caught);
      if (refusal.field === "code") setCodeProblem(refusal.message);
      else setErrors({ [refusal.field]: refusal.message });
      setCode("");
      setBusy(false);
      if (refusal.field !== "form") document.getElementById(refusal.field === "code" ? CODE_ID : fieldId(refusal.field))?.focus();
    }
  }

  return (
    <ActionDialog
      title="Change platform defaults"
      onClose={onClose}
      canClose={(way) => canCloseDialog({ busy }, way)}
      onClosed={() => document.getElementById(CHANGE_ID)?.focus()}
    >
      <form className={styles.dialogForm} onSubmit={submit} noValidate>
        <AiFields
          platform
          catalogue={catalogue}
          saved={reply.defaults}
          defaults={reply.defaults}
          env={reply.env}
          draft={draft}
          errors={errors}
          busy={busy}
          onChange={setDraft}
        />
        <p id="platform-defaults-impact" aria-live="polite">
          {defaultsImpact(reply.defaults, aiBody(draft), reply.orgs_inheriting)}
        </p>
        <CodeField
          id={CODE_ID}
          label="A new 6-digit code from your app"
          value={code}
          onChange={setCode}
          error={codeProblem}
          hint="Saving needs a new code: the one you signed in with does not work again."
        />
        {errors.form && (
          <p id={`${fieldId("form")}-error`} className="error-text" role="alert">
            {errors.form}
          </p>
        )}
        <div className={styles.buttons}>
          <button type="button" className="btn" onClick={onClose} disabled={busy} data-autofocus>
            Cancel
          </button>
          <button
            type="submit"
            className={`btn btn-primary ${busy ? admin.busy : ""}`}
            disabled={busy}
            aria-busy={busy || undefined}
            aria-describedby="platform-defaults-impact"
          >
            {busy && <span className="spinner" aria-hidden="true" />}
            Save and apply to organisations still on defaults
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
