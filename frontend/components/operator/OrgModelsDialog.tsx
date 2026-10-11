"use client";

// One organisation's AI models, for the operator (model choice, council 11 Oct 2026, MS1,
// MS2, SP4): which enabled models its scans may be read with (checkboxes), its default
// scan model (radios, among the ticked ones) and its monthly budget in US dollars (warn
// only). No models: the platform's model reads its scans, as before. A refusal is said in
// plain words; the dialog can't close while saving. Once the models load, the focus moves
// to the first control (the dialog opened on "Loading"), and it goes back to the row's
// "AI models" button when it closes.

import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { getOperatorModels, getOperatorOrgModels, putOperatorOrgModels } from "@/lib/api";
import type { OperatorModel, Organisation } from "@/lib/types";
import { canCloseDialog, operatorErrorMessage } from "./logic";
import { bodyOf, draftErrors, draftOf, offered, toggled } from "./models";
import type { DraftErrors, OrgModelsDraft } from "./models";
import styles from "./Operator.module.css";

/** The row's "AI models" button, where the focus goes back. */
export const orgModelsFocusId = (id: string) => `org-models-${id}`;

const NO_ERRORS: DraftErrors = { default_scan: null, budget: null };

export function OrgModelsDialog({ org, onClose, onSaved }: { org: Organisation; onClose(): void; onSaved(message: string): void }) {
  const [loaded, setLoaded] = useState<{ catalogue: OperatorModel[]; saved: string[] } | null>(null);
  const [draft, setDraft] = useState<OrgModelsDraft | null>(null);
  const [loadFailed, setLoadFailed] = useState<string | null>(null);
  const [errors, setErrors] = useState<DraftErrors>(NO_ERRORS);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // The form appears once the data has loaded: its first control takes the focus then.
  const focusFirst = useCallback((form: HTMLFormElement | null) => {
    form?.querySelector<HTMLElement>("input, select, button")?.focus();
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getOperatorModels(), getOperatorOrgModels(org.id)]).then(
      ([catalogue, saved]) => {
        if (cancelled) return;
        setLoaded({ catalogue: catalogue.items, saved: saved.models });
        setDraft(draftOf(saved));
        setLoadFailed(null);
      },
      (caught: unknown) => !cancelled && setLoadFailed(operatorErrorMessage(caught)),
    );
    return () => {
      cancelled = true;
    };
  }, [org.id, attempt]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !draft) return;
    setFailure(null);
    const found = draftErrors(draft);
    setErrors(found);
    if (found.default_scan || found.budget) {
      document.getElementById(found.default_scan ? "org-models-default" : "org-models-budget")?.focus();
      return;
    }
    setBusy(true);
    try {
      await putOperatorOrgModels(org.id, bodyOf(draft));
      onSaved(`Saved the AI models of ${org.name}.`);
      onClose();
    } catch (caught) {
      setFailure(operatorErrorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <ActionDialog
      title={`AI models of ${org.name}`}
      onClose={onClose}
      canClose={(way) => canCloseDialog({ busy }, way)}
      onClosed={() => document.getElementById(orgModelsFocusId(org.id))?.focus()}
    >
      {loadFailed && (
        <div className="stack">
          <p className="error-text" role="alert">
            {loadFailed}
          </p>
          <div>
            <button type="button" className="btn" data-autofocus onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          </div>
        </div>
      )}
      {!loaded && !loadFailed && (
        <p className="muted" role="status">
          Loading the models…
        </p>
      )}
      {loaded && draft && (
        <form ref={focusFirst} className={styles.dialogForm} onSubmit={submit} noValidate>
          <OrgModelsFields
            models={offered(loaded.catalogue, loaded.saved)}
            draft={draft}
            errors={errors}
            busy={busy}
            onChange={(next) => setDraft(next)}
          />
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
              Save
            </button>
          </div>
        </form>
      )}
    </ActionDialog>
  );
}

export function OrgModelsFields(props: {
  models: OperatorModel[];
  draft: OrgModelsDraft;
  errors: DraftErrors;
  busy: boolean;
  onChange(draft: OrgModelsDraft): void;
}) {
  const { models, draft, errors, busy, onChange } = props;
  const ticked = models.filter((model) => draft.models.includes(model.id));
  return (
    <>
      <fieldset className={styles.choices} disabled={busy}>
        <legend className="label">Models its scans may be read with</legend>
        {models.length === 0 ? (
          <p className="muted">No model is enabled on the platform yet. Enable one in the Models tab.</p>
        ) : (
          models.map((model, index) => (
            <label key={model.id} className={styles.choice}>
              <input
                type="checkbox"
                checked={draft.models.includes(model.id)}
                data-autofocus={index === 0 || undefined}
                onChange={(event) => onChange(toggled(draft, model.id, event.target.checked))}
              />
              <span>{model.name}</span>
            </label>
          ))
        )}
        {draft.models.length === 0 && (
          <p className="hint">None ticked: its scans are read by the platform&apos;s model, as before.</p>
        )}
      </fieldset>
      {ticked.length > 0 && (
        <fieldset className={styles.choices} disabled={busy}>
          <legend className="label">Default scan model</legend>
          {ticked.map((model, index) => (
            <label key={model.id} className={styles.choice}>
              <input
                type="radio"
                name="org-models-default"
                id={index === 0 ? "org-models-default" : undefined}
                checked={draft.default_scan === model.id}
                aria-describedby={errors.default_scan ? "org-models-default-error" : undefined}
                onChange={() => onChange({ ...draft, default_scan: model.id })}
              />
              <span>{model.name}</span>
            </label>
          ))}
          {errors.default_scan && (
            <span id="org-models-default-error" className="error-text" role="alert">
              {errors.default_scan}
            </span>
          )}
        </fieldset>
      )}
      <div className="field">
        <label htmlFor="org-models-budget">Monthly AI budget, in US dollars</label>
        <input
          id="org-models-budget"
          className="input"
          type="number"
          inputMode="decimal"
          min="0.01"
          step="0.01"
          value={draft.budget}
          disabled={busy}
          onChange={(event) => onChange({ ...draft, budget: event.target.value })}
          aria-invalid={errors.budget ? true : undefined}
          aria-describedby={errors.budget ? "org-models-budget-hint org-models-budget-error" : "org-models-budget-hint"}
        />
        <span id="org-models-budget-hint" className="hint">
          Warn only: its admin is warned at 80% and 100%; nothing stops. Leave it empty for no budget.
        </span>
        {errors.budget && (
          <span id="org-models-budget-error" className="error-text">
            {errors.budget}
          </span>
        )}
      </div>
    </>
  );
}
