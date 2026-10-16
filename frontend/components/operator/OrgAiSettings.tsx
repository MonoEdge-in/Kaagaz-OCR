"use client";

// One organisation's AI settings, for the operator (features 25-27, council 15 Oct 2026,
// C10, C12; replaces the "AI models" dialog of model choice). Two areas, each following
// the platform defaults until it is given its own: Scanning (the models its scans may be
// read with, the default, the thinking level, Cost saving) and Answering (the models people may
// ask, the default, the thinking levels people may choose and the default level). Then
// Ensemble (the organisation's own switch, never a default) and the monthly budget in US
// dollars: money is shown here, in the operator console, and nowhere an organisation sees.
// A status line says which areas are its own. "Use platform defaults" empties both areas.
// Each section says "Same as platform default" or "Changed from platform default", with
// "Reset section" (board 9). Answering shows what the organisation's people will see, and
// the budget its rough rupees and this month's spend. A save bar at the foot lists the
// unsaved changes, with Discard and Save (both off while nothing changed). Closing it (Esc,
// a backdrop click or Close) with unsaved changes asks first, in the save bar.
// Saving asks for a fresh code (step-up); a refusal is said next to the field it is about.
// Once the settings load, the focus moves to the first control, and it goes back to the
// row's "AI settings" button when the dialog closes. AiFields is shared with the platform
// defaults (PlatformDefaults.tsx).

import { useCallback, useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { CodeField } from "@/app/login/CodeField";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { ApiError, getOperatorInsights, getOperatorModels, getOrgAi, putOrgAi } from "@/lib/api";
import type { AiEnv, AiLevel, AiValues, OperatorModel, Organisation, OrgAiReply } from "@/lib/types";
import { canCloseDialog, countText, operatorErrorMessage } from "./logic";
import {
  COST_SAVING,
  DYNAMIC_HINT,
  LEVELS,
  LEVEL_WORDS,
  VENDOR_NOT_ALLOWED,
  aiBody,
  aiErrors,
  changesText,
  draftOf,
  followDefaults,
  inheritedText,
  inrText,
  monthSpentText,
  offered,
  parseBudget,
  pickerText,
  realCostText,
  refusalField,
  resetSection,
  sameAsDefault,
  statusText,
  toggled,
  toggledLevel,
} from "./models";
import type { AiDraft, AiErrors, AiField, Area, Section } from "./models";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

/** The row's "AI settings" button, where the focus goes back. */
export const orgAiFocusId = (id: string) => `org-ai-${id}`;

/** A field's id in the form; its error is `${fieldId(field)}-error`. */
export const fieldId = (field: AiField) => `ai-${field.replace(/_/g, "-")}`;
export const CODE_ID = "ai-code";
const STEP_UP_CODES = new Set(["step_up_failed", "step_up_locked", "locked"]);

/** Where a refused save's words go, and the focus with them. */
export function refusalOf(caught: unknown): { field: AiField | "code"; message: string } {
  const message = operatorErrorMessage(caught);
  if (!(caught instanceof ApiError)) return { field: "form", message };
  return { field: STEP_UP_CODES.has(caught.code) ? "code" : refusalField(caught.code), message };
}

/** The first field with a problem, in the form's order, for the focus. */
export function firstProblem(errors: AiErrors): AiField | null {
  const order: AiField[] = ["scan_models", "scan_default", "chat_models", "chat_default", "chat_levels", "budget", "form"];
  return order.find((field) => errors[field]) ?? null;
}

export function OrgAiSettings({ org, onClose, onSaved }: { org: Organisation; onClose(): void; onSaved(message: string): void }) {
  const [loaded, setLoaded] = useState<{ catalogue: OperatorModel[]; reply: OrgAiReply; spent: string | null } | null>(null);
  const [draft, setDraft] = useState<AiDraft | null>(null);
  const [loadFailed, setLoadFailed] = useState<string | null>(null);
  const [errors, setErrors] = useState<AiErrors>({});
  const [code, setCode] = useState("");
  const [codeProblem, setCodeProblem] = useState<string | null>(null);
  const [reset, setReset] = useState(false);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [leaving, setLeaving] = useState(false); // asked to close with unsaved changes
  // The form appears once the data has loaded: its first control takes the focus then.
  const focusFirst = useCallback((form: HTMLFormElement | null) => {
    form?.querySelector<HTMLElement>("input, select, button")?.focus();
  }, []);

  useEffect(() => {
    let cancelled = false;
    // This month's spend comes from the insights (one day is the smallest ask); without them the line is left out.
    Promise.all([getOperatorModels(), getOrgAi(org.id), getOperatorInsights(1).catch(() => null)]).then(
      ([catalogue, reply, insights]) => {
        if (cancelled) return;
        const spent = monthSpentText(insights?.organisations.find((each) => each.organisation_id === org.id));
        setLoaded({ catalogue: catalogue.items, reply, spent });
        setDraft(draftOf(reply.own, reply));
        setLoadFailed(null);
      },
      (caught: unknown) => !cancelled && setLoadFailed(operatorErrorMessage(caught)),
    );
    return () => {
      cancelled = true;
    };
  }, [org.id, attempt]);

  const saved = loaded ? draftOf(loaded.reply.own, loaded.reply) : null;
  const changes = saved && draft ? changesText(saved, draft, loaded?.catalogue ?? []) : [];
  const unchanged = changes.length === 0;
  const asking = leaving && !unchanged;
  // Keep editing takes the focus when the question opens: never the button that loses the changes.
  const focusKeep = useCallback((button: HTMLButtonElement | null) => button?.focus(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !draft || unchanged) return;
    const found = aiErrors(draft, false);
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
      await putOrgAi(org.id, { ...aiBody(draft), ensemble: draft.ensemble, ai_budget_usd: parseBudget(draft.budget) ?? null }, normalizeCode(code));
      onSaved(`Saved the AI settings of ${org.name}.`);
      onClose();
    } catch (caught) {
      const refusal = refusalOf(caught);
      if (refusal.field === "code") setCodeProblem(refusal.message);
      else setErrors({ [refusal.field]: refusal.message });
      setCode("");
      setBusy(false);
      // A refusal of the whole form is said by its alert; the focus stays on Save.
      if (refusal.field !== "form") document.getElementById(refusal.field === "code" ? CODE_ID : fieldId(refusal.field))?.focus();
    }
  }

  return (
    <ActionDialog
      title={`AI settings of ${org.name}`}
      onClose={() => (unchanged ? onClose() : setLeaving(true))}
      canClose={(way) => canCloseDialog({ busy }, way)}
      onClosed={() => document.getElementById(orgAiFocusId(org.id))?.focus()}
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
          Loading the AI settings…
        </p>
      )}
      {loaded && draft && (
        <form ref={focusFirst} className={styles.dialogForm} onSubmit={submit} noValidate>
          <p role="status">{statusText(aiBody(draft))}</p>
          <div>
            <button
              type="button"
              className="btn"
              disabled={busy}
              aria-describedby={reset ? "ai-reset-note" : undefined}
              onClick={() => {
                setDraft(followDefaults(draft));
                setErrors({});
                setReset(true);
              }}
            >
              Use platform defaults
            </button>
            {reset && (
              <p id="ai-reset-note" className="hint" aria-live="polite">
                Scanning and answering will both follow the platform defaults, and Ensemble is off. Save to apply.
              </p>
            )}
          </div>
          <AiFields
            platform={false}
            orgName={org.name}
            spent={loaded.spent}
            catalogue={loaded.catalogue}
            saved={loaded.reply.own}
            defaults={loaded.reply.defaults}
            env={loaded.reply.env}
            maxAgents={loaded.reply.max_agents}
            draft={draft}
            errors={errors}
            busy={busy}
            onChange={(next) => {
              setDraft(next);
              setReset(false);
            }}
          />
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
          <div className={styles.saveBar}>
            {asking && (
              <div className={styles.leaveAsk} role="alert">
                <p>Close without saving? Your {countText(changes.length, "unsaved change", "unsaved changes")} will be lost.</p>
                <div className={styles.buttons}>
                  <button type="button" className={`btn btn-danger ${styles.destructive}`} onClick={onClose}>
                    Close without saving
                  </button>
                  <button ref={focusKeep} type="button" className="btn" onClick={() => setLeaving(false)}>
                    Keep editing
                  </button>
                </div>
              </div>
            )}
            <p className={styles.changes}>
              <strong id="ai-changes-count" aria-live="polite">
                {unchanged ? "No unsaved changes" : countText(changes.length, "unsaved change", "unsaved changes")}
              </strong>
              {!unchanged && <span id="ai-changes"> {changes.join(" · ")}</span>}
            </p>
            <div className={styles.buttons}>
              <button
                type="button"
                className="btn"
                disabled={busy || unchanged}
                onClick={(event) => {
                  // The button goes off: the focus moves to the form's first control.
                  event.currentTarget.form?.querySelector<HTMLElement>("input, select, button")?.focus();
                  setDraft(saved);
                  setErrors({});
                  setReset(false);
                }}
              >
                Discard
              </button>
              <button
                type="submit"
                className={`btn btn-primary ${busy ? admin.busy : ""}`}
                disabled={busy || unchanged}
                aria-busy={busy || undefined}
                aria-describedby={unchanged ? "ai-changes-count" : "ai-changes-count ai-changes"}
              >
                {busy && <span className="spinner" aria-hidden="true" />}
                Save
              </button>
            </div>
          </div>
        </form>
      )}
    </ActionDialog>
  );
}

/** A field's error, said next to it; its id is what the field's aria-describedby names. */
function FieldError({ field, errors }: { field: AiField; errors: AiErrors }) {
  return errors[field] ? (
    <span id={`${fieldId(field)}-error`} className="error-text" role="alert">
      {errors[field]}
    </span>
  ) : null;
}

const describedBy = (field: AiField, errors: AiErrors, hint?: string) =>
  [errors[field] ? `${fieldId(field)}-error` : "", hint ?? ""].filter(Boolean).join(" ") || undefined;

/** One section of the fields. An organisation's says where it stands against the platform
 * default and, once changed, offers "Reset section" (board 9); the platform's says neither. */
function SectionBox(props: {
  title: string;
  section: Section;
  platform: boolean;
  draft: AiDraft;
  busy: boolean;
  onChange(draft: AiDraft): void;
  children: ReactNode;
}) {
  const { title, section, platform, draft, busy, onChange, children } = props;
  const same = sameAsDefault(draft, section);
  return (
    <fieldset className={styles.area} disabled={busy}>
      <legend>
        {title}
        {!platform && <span className={styles.sectionTag}>{same ? "Same as platform default" : "Changed from platform default"}</span>}
      </legend>
      {!platform && !same && (
        <div className={styles.sectionTools}>
          <button
            type="button"
            className="btn"
            aria-label={`Reset section: ${title}`}
            onClick={(event) => {
              // The button goes once the section is back: the focus moves to its first field.
              event.currentTarget.closest("fieldset")?.querySelector<HTMLElement>("input, select")?.focus();
              onChange(resetSection(draft, section));
            }}
          >
            Reset section
          </button>
        </div>
      )}
      {children}
    </fieldset>
  );
}

/** The two areas' fields. ``platform``: the defaults (no Ensemble, no budget; an empty
 * list is the server's model); else an organisation's (an empty list or "Platform
 * default" follows ``defaults``). */
export function AiFields(props: {
  platform: boolean;
  orgName?: string; // an organisation's: "People at <name> will see"
  spent?: string | null; // an organisation's: "This month: $X used", when known
  catalogue: OperatorModel[];
  saved: AiValues;
  defaults: AiValues;
  env: AiEnv;
  maxAgents?: number;
  draft: AiDraft;
  errors: AiErrors;
  busy: boolean;
  onChange(draft: AiDraft): void;
}) {
  const { platform, orgName, spent, catalogue, saved, defaults, env, maxAgents, draft, errors, busy, onChange } = props;
  const from = (field: Parameters<typeof inheritedText>[0]) => inheritedText(field, defaults, env, catalogue);
  const section = { platform, draft, busy, onChange };
  const rupees = inrText(parseBudget(draft.budget));
  return (
    <>
      <SectionBox title="Scanning" section="scan" {...section}>
        <ModelChoices
          area="scan"
          title="Models its scans may be read with"
          none={platform ? `None ticked: the server's model (${env.scan_model}).` : `None ticked: from platform defaults, ${from("scan_models")}.`}
          models={offered(catalogue, "scan", saved.scan_models)}
          draft={draft}
          errors={errors}
          onChange={onChange}
        />
        <div className="field">
          <label htmlFor="ai-scan-thinking">Thinking level</label>
          <select
            id="ai-scan-thinking"
            className="input"
            value={draft.scan_thinking ?? ""}
            onChange={(event) => onChange({ ...draft, scan_thinking: (event.target.value || null) as AiLevel | null })}
          >
            <option value="">{platform ? `The server's level (${env.scan_thinking})` : `Platform default (${from("scan_thinking")})`}</option>
            {LEVELS.map((level) => (
              <option key={level} value={level}>
                {LEVEL_WORDS[level]}
              </option>
            ))}
          </select>
        </div>
        {platform ? (
          <label className={styles.choice}>
            <input
              type="checkbox"
              checked={draft.dynamic === true}
              aria-describedby="ai-dynamic-hint"
              onChange={(event) => onChange({ ...draft, dynamic: event.target.checked })}
            />
            <span>{COST_SAVING}</span>
          </label>
        ) : (
          <div className="field">
            <label htmlFor="ai-dynamic">{COST_SAVING}</label>
            <select
              id="ai-dynamic"
              className="input"
              value={draft.dynamic === null ? "" : draft.dynamic ? "on" : "off"}
              aria-describedby="ai-dynamic-hint"
              onChange={(event) => onChange({ ...draft, dynamic: event.target.value === "" ? null : event.target.value === "on" })}
            >
              <option value="">Platform default ({from("dynamic")})</option>
              <option value="on">On</option>
              <option value="off">Off</option>
            </select>
          </div>
        )}
        <span id="ai-dynamic-hint" className="hint">
          {DYNAMIC_HINT} Needs at least two scanning models.
        </span>
      </SectionBox>

      <SectionBox title="Answering" section="chat" {...section}>
        <ModelChoices
          area="chat"
          title="Models people may ask"
          none={platform ? `None ticked: the server's model (${env.chat_model}).` : `None ticked: from platform defaults, ${from("chat_models")}.`}
          models={offered(catalogue, "chat", saved.chat_models)}
          draft={draft}
          errors={errors}
          onChange={onChange}
        />
        <fieldset className={styles.choices}>
          <legend className="label">Thinking levels people may choose</legend>
          {LEVELS.map((level, index) => (
            <label key={level} className={styles.choice}>
              <input
                type="checkbox"
                id={index === 0 ? fieldId("chat_levels") : undefined}
                checked={draft.chat_levels.includes(level)}
                aria-describedby={describedBy("chat_levels", errors)}
                onChange={(event) => onChange(toggledLevel(draft, level, event.target.checked))}
              />
              <span>{LEVEL_WORDS[level]}</span>
            </label>
          ))}
          {!platform && draft.chat_levels.length === 0 && (
            <p className="hint">None ticked: from platform defaults, {from("chat_levels")}.</p>
          )}
          <FieldError field="chat_levels" errors={errors} />
        </fieldset>
        {draft.chat_levels.length > 0 && (
          <div className="field">
            <label htmlFor="ai-chat-default-level">Default level</label>
            <select
              id="ai-chat-default-level"
              className="input"
              value={draft.chat_default_level ?? ""}
              onChange={(event) => onChange({ ...draft, chat_default_level: event.target.value as AiLevel })}
            >
              {draft.chat_levels.map((level) => (
                <option key={level} value={level}>
                  {LEVEL_WORDS[level]}
                </option>
              ))}
            </select>
          </div>
        )}
        {orgName && <p className="hint">{pickerText(orgName, draft, defaults, env, catalogue)}</p>}
      </SectionBox>

      {!platform && (
        <>
          <SectionBox title="Ensemble" section="ensemble" {...section}>
            <div className="field">
              <label className={styles.choice}>
                <input
                  type="checkbox"
                  checked={draft.ensemble}
                  disabled={busy}
                  aria-describedby="ai-ensemble-hint"
                  onChange={(event) => onChange({ ...draft, ensemble: event.target.checked })}
                />
                <span>Ensemble: models work together</span>
              </label>
              <span id="ai-ensemble-hint" className="hint">
                Up to {maxAgents} models. Scanning: the main model reads each page; every value that is ready to verify is
                read by the others too, and the value most of them read is kept, or it stays Ready to verify. Answering:
                people may ask more than one model. Never a platform default.
              </span>
            </div>
          </SectionBox>
          <SectionBox title="Budget" section="budget" {...section}>
            <div className="field">
              <label htmlFor={fieldId("budget")}>Monthly AI budget, in US dollars</label>
              <input
                id={fieldId("budget")}
                className="input"
                type="number"
                inputMode="decimal"
                min="0.01"
                step="0.01"
                value={draft.budget}
                disabled={busy}
                // The wheel scrolls the dialog, never the budget: the box lets go of the focus first.
                onWheel={(event) => event.currentTarget.blur()}
                onChange={(event) => onChange({ ...draft, budget: event.target.value })}
                aria-invalid={errors.budget ? true : undefined}
                aria-describedby={describedBy("budget", errors, rupees ? "ai-budget-hint ai-budget-inr" : "ai-budget-hint")}
              />
              <span id="ai-budget-hint" className="hint">
                Warn only, and seen only here. Leave it empty for no budget.
              </span>
              {rupees && (
                <span id="ai-budget-inr" className="hint">
                  {rupees}
                </span>
              )}
              <FieldError field="budget" errors={errors} />
            </div>
            {spent && <p className="hint">{spent}</p>}
          </SectionBox>
        </>
      )}
    </>
  );
}

/** One area's models (checkboxes, by name) and its default (radios, among the ticked). */
function ModelChoices(props: {
  area: Area;
  title: string;
  none: string;
  models: OperatorModel[];
  draft: AiDraft;
  errors: AiErrors;
  onChange(draft: AiDraft): void;
}) {
  const { area, title, none, models, draft, errors, onChange } = props;
  const list = `${area}_models` as const;
  const fallback = `${area}_default` as const;
  const chosen = draft[list];
  const ticked = models.filter((model) => chosen.includes(model.id));
  return (
    <>
      <fieldset className={styles.choices}>
        <legend className="label">{title}</legend>
        {models.length === 0 ? (
          <p className="muted">No model can do this on the platform yet. Enable one in the Models tab.</p>
        ) : (
          models.map((model, index) => (
            <label key={model.id} className={styles.choice}>
              <input
                type="checkbox"
                id={index === 0 ? fieldId(list) : undefined}
                checked={chosen.includes(model.id)}
                aria-describedby={describedBy(list, errors)}
                onChange={(event) => onChange(toggled(draft, area, model.id, event.target.checked))}
              />
              <span>
                {model.name}
                <span className={`${styles.statusNote} tabular`}>{realCostText(model, area)}</span>
                {!model.vendor_allowed && <span className={styles.statusNote}>{VENDOR_NOT_ALLOWED}</span>}
              </span>
            </label>
          ))
        )}
        {chosen.length === 0 && <p className="hint">{none}</p>}
        <FieldError field={list} errors={errors} />
      </fieldset>
      {ticked.length > 0 && (
        <fieldset className={styles.choices}>
          <legend className="label">Default {area === "scan" ? "scanning" : "answering"} model</legend>
          {ticked.map((model, index) => (
            <label key={model.id} className={styles.choice}>
              <input
                type="radio"
                name={fieldId(fallback)}
                id={index === 0 ? fieldId(fallback) : undefined}
                checked={draft[fallback] === model.id}
                aria-describedby={describedBy(fallback, errors)}
                onChange={() => onChange({ ...draft, [fallback]: model.id })}
              />
              <span>{model.name}</span>
            </label>
          ))}
          <FieldError field={fallback} errors={errors} />
        </fieldset>
      )}
    </>
  );
}
