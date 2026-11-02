"use client";

// Platform defaults, in the Models tab (features 25-27, council 15 Oct 2026, C10, C12): what
// every organisation that has not been given its own settings uses, per area (scanning:
// models, default, thinking level, Cost saving; answering: models, default, levels people may
// choose, default level), with how many organisations follow each area now. "Change
// platform defaults" opens the same fields as an organisation's (AiFields), without
// Ensemble and budget; before saving it says which organisations the change reaches, and
// it asks for a fresh code (step-up). A refusal is said next to the field it is about. The
// focus goes back to the button when the dialog closes.
// Scan cost settings (#463, Tushar, 31 Oct 2026): the same scanning fields and Cost per page
// panel as an organisation's (without its accuracy check), and the Save confirmation says
// the cost a page now.
// Who reads the pages (#473): the default reader and Mistral model with the same fields, its
// row in the summary, and the banner while Mistral has stopped.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { CodeField } from "@/app/login/CodeField";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { getAiDefaults, postScanCost, putAiDefaults } from "@/lib/api";
import type { AiDefaults, OperatorModel } from "@/lib/types";
import { LoadFailed } from "./LoadFailed";
import { canCloseDialog, countText, operatorErrorMessage } from "./logic";
import { COST_SAVING, KEEP_OWN, aiBody, aiErrors, costAsk, defaultsImpact, draftOf, inheritedText, withCost } from "./models";
import type { AiDraft, AiErrors } from "./models";
import { AiFields, CODE_ID, MistralStopped, fieldId, firstProblem, refusalOf } from "./OrgAiSettings";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

const CHANGE_ID = "platform-defaults-change";
const IMPACT_ID = "platform-defaults-impact";
// Short, so it never clips or wraps on a narrow screen (MonoEdge buttons: nowrap labels
// that fit); the impact list above it says what it applies to.
export const SAVE_LABEL = "Save and apply";

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
          <MistralStopped status={reply.env.mistral_status} />
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
          onSaved={(next, message) => {
            setReply(next);
            onSaved(message);
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
    { label: "Who reads the pages", value: inheritedText("scan_reader", defaults, env, catalogue), count: counts.scan_reader },
    { label: "Scanning models", value: inheritedText("scan_models", defaults, env, catalogue), count: counts.scan_models },
    { label: "Scanning thinking level", value: inheritedText("scan_thinking", defaults, env, catalogue), count: counts.scan_thinking },
    { label: "Picture detail", value: inheritedText("scan_resolution", defaults, env, catalogue), count: counts.scan_resolution },
    {
      label: "Thinking for pages read again",
      value: inheritedText("careful_thinking", defaults, env, catalogue),
      count: counts.careful_thinking,
    },
    {
      label: "Picture detail for pages read again",
      value: inheritedText("careful_resolution", defaults, env, catalogue),
      count: counts.careful_resolution,
    },
    {
      label: "Most output tokens a page",
      value: inheritedText("page_max_output_tokens", defaults, env, catalogue),
      count: counts.page_max_output_tokens,
    },
    { label: COST_SAVING, value: inheritedText("dynamic", defaults, env, catalogue), count: counts.dynamic },
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

function DefaultsDialog(props: {
  reply: AiDefaults;
  catalogue: OperatorModel[];
  onClose(): void;
  onSaved(reply: AiDefaults, message: string): void;
}) {
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
      const next = await putAiDefaults(aiBody(draft), normalizeCode(code));
      // Saved: the confirmation says the cost a page now; without it, it just says saved.
      const cost = await postScanCost(costAsk(null, next.defaults)).catch(() => null);
      onSaved(next, withCost("Saved the platform defaults.", cost, false));
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
      wide
      title="Change platform defaults"
      onClose={onClose}
      canClose={(way) => canCloseDialog({ busy }, way)}
      onClosed={() => document.getElementById(CHANGE_ID)?.focus()}
    >
      <form className={styles.dialogForm} onSubmit={submit} noValidate>
        <AiFields
          platform
          customerId={null}
          catalogue={catalogue}
          saved={reply.defaults}
          defaults={reply.defaults}
          env={reply.env}
          draft={draft}
          errors={errors}
          busy={busy}
          onChange={setDraft}
        />
        <DefaultsImpact id={IMPACT_ID} impact={defaultsImpact(reply.defaults, aiBody(draft), reply.orgs_inheriting)} />
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
            aria-describedby={IMPACT_ID}
          >
            {busy && <span className="spinner" aria-hidden="true" />}
            {SAVE_LABEL}
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}

/** What a defaults save changes, as a short list the save button points at. */
export function DefaultsImpact({ id, impact }: { id: string; impact: ReturnType<typeof defaultsImpact> }) {
  return (
    <div id={id} className={styles.impact} aria-live="polite">
      <p>{impact.lead}</p>
      {impact.areas.length > 0 && (
        <>
          <ul>
            {impact.areas.map((area) => (
              <li key={area}>{area}</li>
            ))}
          </ul>
          <p>{KEEP_OWN}</p>
        </>
      )}
    </div>
  );
}
