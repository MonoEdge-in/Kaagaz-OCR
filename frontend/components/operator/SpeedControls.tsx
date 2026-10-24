"use client";

// Speed, a tab of /operator (package S, 0053): the platform's model requests per minute,
// documents read at the same time and pages of one document read at the same time, changed
// live with no redeploy. Each is empty for the server's setting and never above it: the most
// this server allows is set when it is deployed (MODEL_RPM, WORKER_REPLICAS,
// OCR_PAGE_CONCURRENCY). Saving asks first in ConfirmDialog with a fresh code (step-up).
// The number of worker containers is not changed here. Super admin only.

import { useEffect, useState } from "react";
import { Toast } from "@/components/ui/Toast";
import { getSpeed, setSpeed } from "@/lib/api";
import type { Speed, SpeedName } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { LoadFailed } from "./LoadFailed";
import { afterDone, operatorErrorMessage } from "./logic";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

export const SPEED_LABELS: Record<SpeedName, string> = {
  model_rpm: "Model requests per minute (whole platform)",
  documents_at_once: "Documents read at the same time",
  pages_at_once: "Pages of one document read at the same time",
};
const NAMES = Object.keys(SPEED_LABELS) as SpeedName[];
export const DEPLOYED = "The most this server allows is set when it is deployed. You can only go lower.";
export const NO_CONTAINERS = "Adding or removing worker containers is not done here.";
export const FEW_PLACES =
  "With 2 or fewer documents at the same time, one organisation's large upload can take every place, and other organisations wait until one frees.";
const SAVE_ID = "speed-save";
const fieldId = (name: SpeedName) => `speed-${name}`;

type Draft = Record<SpeedName, string>;

/** What a field holds: null for empty (the server's setting), a whole number from 1 to the most,
 * or undefined when it is neither. */
export function parseSpeed(text: string, most: number): number | null | undefined {
  const value = text.trim();
  if (value === "") return null;
  if (!/^\d+$/.test(value)) return undefined;
  const number = Number(value);
  return number >= 1 && number <= most ? number : undefined;
}

export const speedHint = (saved: number | null, most: number) =>
  saved === null
    ? `Using the server's setting: ${most}.`
    : `The most this server allows is ${most}. Leave it empty to use the server's setting.`;

/** Saving: asked first, with a fresh code. */
export function speedConfirm(values: Record<SpeedName, number | null>): Confirm {
  // One organisation may read 2 documents at once (MAX_ACTIVE_PER_CUSTOMER), so at 2 or fewer
  // its batch can take every place: said plainly rather than shared out.
  const few = values.documents_at_once !== null && values.documents_at_once <= 2;
  return {
    title: "Save the speed settings?",
    effect: `The next model request, the next document and the next document's pages use them. Documents being read now finish as they started.${few ? ` ${FEW_PLACES}` : ""}`,
    action: "Save",
    done: "The speed settings are saved.",
    danger: false,
    stepUp: true,
    focusId: SAVE_ID,
    run: (code) => setSpeed(values, code),
  };
}

/** The three fields and Save, which opens the confirm. */
export function SpeedForm({ speed, onSave }: { speed: Speed; onSave(values: Record<SpeedName, number | null>): void }) {
  const [draft, setDraft] = useState<Draft>(() => {
    const start = {} as Draft;
    for (const name of NAMES) start[name] = speed[name].value === null ? "" : String(speed[name].value);
    return start;
  });
  const parsed = Object.fromEntries(NAMES.map((name) => [name, parseSpeed(draft[name], speed[name].most)]));
  const valid = NAMES.every((name) => parsed[name] !== undefined);
  const changed = NAMES.some((name) => parsed[name] !== speed[name].value);
  return (
    <div className={styles.dialogForm}>
      {NAMES.map((name) => {
        const bad = parsed[name] === undefined;
        const most = speed[name].most;
        return (
          <div key={name} className="field">
            <label htmlFor={fieldId(name)}>{SPEED_LABELS[name]}</label>
            <input
              id={fieldId(name)}
              className="input"
              inputMode="numeric"
              value={draft[name]}
              placeholder={`Server's setting (${most})`}
              onChange={(event) => setDraft({ ...draft, [name]: event.target.value })}
              aria-invalid={bad ? true : undefined}
              aria-describedby={`${fieldId(name)}-hint${bad ? ` ${fieldId(name)}-error` : ""}`}
            />
            <span id={`${fieldId(name)}-hint`} className="hint">
              {speedHint(speed[name].value, most)}
            </span>
            {bad && (
              <span id={`${fieldId(name)}-error`} className="error-text" role="alert">
                Enter a whole number from 1 to {most}, or leave it empty.
              </span>
            )}
          </div>
        );
      })}
      <div>
        <button
          id={SAVE_ID}
          type="button"
          className="btn btn-primary"
          disabled={!valid || !changed}
          onClick={() => onSave(parsed as Record<SpeedName, number | null>)}
        >
          Save
        </button>
      </div>
    </div>
  );
}

export function SpeedView() {
  const [attempt, setAttempt] = useState(0);
  const [speed, setSpeedValue] = useState<Speed | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    getSpeed().then(
      (result) => !cancelled && setSpeedValue(result),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);
  const failed = error?.attempt === attempt ? error.message : null;
  return (
    <section className={admin.section} aria-labelledby="speed-heading">
      <h2 id="speed-heading" className={admin.sectionHeading}>
        Speed
      </h2>
      <p className={styles.summary}>How fast the platform reads documents and calls the AI models, for every organisation.</p>
      <p className="hint">{DEPLOYED}</p>
      <p className="hint">{NO_CONTAINERS}</p>
      {failed && !speed && <LoadFailed what="the speed settings" message={failed} onRetry={() => setAttempt((n) => n + 1)} />}
      {!speed && !failed && (
        <p className="visually-hidden" role="status">
          Loading the speed settings…
        </p>
      )}
      {speed && <SpeedForm key={JSON.stringify(speed)} speed={speed} onSave={(values) => setChanging(speedConfirm(values))} />}
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging((open) => afterDone(open, changing));
            setToast(message);
            setAttempt((n) => n + 1);
          }}
        />
      )}
      <Toast message={toast} onClose={() => setToast(null)} />
    </section>
  );
}
