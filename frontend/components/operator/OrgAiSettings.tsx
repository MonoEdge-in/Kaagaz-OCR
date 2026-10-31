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
// Scan cost settings (#463, Tushar, 31 Oct 2026): Scanning is grouped as people think of it,
// First read (models, thinking level, picture detail) then Pages read again (Cost saving,
// their thinking level and picture detail). Each setting says whether it is the
// organisation's own or followed, with "Use platform default" ("Use the server's setting"
// on the platform). Beside the fields on a wide dialog, under them on a phone, the Cost per
// page panel says the measured cost a page at the draft's settings (asked once the draft
// rests; the last reply wins), the change from what is saved while they differ, the pages
// read cleanly, and, for an organisation, its check of accuracy against max settings (with
// a fresh code, polled while it runs). The Save confirmation says the cost and accuracy.

import { useCallback, useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { CodeField } from "@/app/login/CodeField";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import { ActionDialog } from "@/components/documents/ActionDialog";
import {
  ApiError,
  getLatestAccuracyCheck,
  getOperatorInsights,
  getOperatorModels,
  getOrgAi,
  postAccuracyCheck,
  postScanCost,
  putOrgAi,
} from "@/lib/api";
import { usePoll } from "@/lib/usePoll";
import type { AccuracyCheck, AiEnv, AiLevel, AiValues, OperatorModel, Organisation, OrgAiReply } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import { canCloseDialog, countText, operatorErrorMessage } from "./logic";
import {
  APPLIES_NOTE,
  CAREFUL_RESOLUTION_HINT,
  CAREFUL_THINKING_HINT,
  CHECK_LABEL,
  CHECK_PAGES_HINT,
  CHECK_PAGE_CHOICES,
  CHECK_ON_ORG,
  CLEAN_HINT,
  COST_SAVING,
  DYNAMIC_HINT,
  ENSEMBLE_OFF,
  LEVELS,
  LEVEL_WORDS,
  NO_LEVELS,
  RESOLUTIONS,
  RESOLUTION_HINT,
  RESOLUTION_WORDS,
  SCAN_LEVELS,
  VENDOR_NOT_ALLOWED,
  accuracyWords,
  aiBody,
  aiErrors,
  askScanCost,
  changesText,
  checkEffect,
  checkRefusal,
  checkRunning,
  cleanText,
  costAsk,
  costChange,
  costWords,
  draftOf,
  followDefaults,
  hasNoLevels,
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
  sameSettings,
  savedText,
  scanSettings,
  statusText,
  toggled,
  toggledLevel,
  withCost,
} from "./models";
import type { AiDraft, AiErrors, AiField, Area, CheckPages, CostReply, ScanSetting, Section } from "./models";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

/** The row's "AI settings" button, where the focus goes back. */
export const orgAiFocusId = (id: string) => `org-ai-${id}`;

/** A field's id in the form; its error is `${fieldId(field)}-error`. */
export const fieldId = (field: AiField) => `ai-${field.replace(/_/g, "-")}`;
export const CODE_ID = "ai-code";
/** The Accuracy heading: the focus goes there once a check starts (its button goes). */
export const ACCURACY_ID = "ai-accuracy-heading";
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
  const [ensembleOff, setEnsembleOff] = useState(false); // the last save turned Ensemble off
  const [latest, setLatest] = useState<AccuracyCheck | null>(null); // the latest accuracy check, any settings
  const [checking, setChecking] = useState(false); // the check's confirm is open
  const [checkPages, setCheckPages] = useState<CheckPages>(20);
  // Asked when the dialog opens and once a check starts; again every few seconds only while
  // it runs, and not while the tab is hidden (lib/poll).
  const poll = usePoll<AccuracyCheck | null>({
    load: () => getLatestAccuracyCheck(org.id),
    onResult: setLatest,
    onFailure: () => undefined, // the panel keeps what it showed; the next poll asks again
    again: checkRunning,
    signature: (check) => `${check?.id}:${check?.status}`,
  });
  useEffect(() => poll.refresh(), [poll, org.id]);
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
      const { ensemble_off: ensembleTurnedOff, ...reply } = await putOrgAi(org.id, { ...aiBody(draft), ensemble: draft.ensemble, ai_budget_usd: parseBudget(draft.budget) ?? null }, normalizeCode(code));
      // Saved: the confirmation says the cost a page and the accuracy now; without them, it just says saved.
      const cost = await postScanCost(costAsk(org.id, reply.own)).catch(() => null);
      onSaved(withCost(savedText(org.name, ensembleTurnedOff), cost, true));
      if (!ensembleTurnedOff || !loaded) {
        onClose();
        return;
      }
      // Saved, with Ensemble off (fewer than two scanning models): the dialog stays open, as
      // saved, so the note is seen.
      setLoaded({ ...loaded, reply });
      setDraft(draftOf(reply.own, reply));
      setEnsembleOff(true);
      setCode("");
      setBusy(false);
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
      wide
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
            customerId={org.id}
            check={{ latest, onCheck: () => setChecking(true) }}
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
              setEnsembleOff(false);
            }}
          />
          {ensembleOff && (
            <p className="hint" role="status">
              {ENSEMBLE_OFF}
            </p>
          )}
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
      {/* Outside the form: its own form's submit must never reach the settings' Save. */}
      {checking && draft && (
        <ConfirmDialog
          confirm={{
            title: "Check accuracy against max settings?",
            effect: checkEffect(org.name, latest, checkPages),
            fields: (
              <fieldset className={styles.choices}>
                <legend className="label">Pages to check</legend>
                {CHECK_PAGE_CHOICES.map((pages) => (
                  <label key={pages} className={styles.choice}>
                    <input
                      type="radio"
                      name="ai-check-pages"
                      checked={checkPages === pages}
                      aria-describedby="ai-check-pages-hint"
                      onChange={() => setCheckPages(pages)}
                    />
                    <span>{pages} pages</span>
                  </label>
                ))}
                <span id="ai-check-pages-hint" className="hint">
                  {CHECK_PAGES_HINT}
                </span>
              </fieldset>
            ),
            action: "Start the check",
            done: "Started the accuracy check.",
            danger: false,
            stepUp: true,
            focusId: ACCURACY_ID,
            run: (stepUpCode) => postAccuracyCheck(org.id, { ...scanSettings(draft), pages: checkPages }, stepUpCode),
            errorText: checkRefusal,
          }}
          onClose={() => setChecking(false)}
          onDone={() => {
            setChecking(false);
            poll.refresh(); // it runs now: "Checking 50 pages…"
          }}
        />
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
  customerId: string | null; // the organisation's; null: the platform defaults
  check?: { latest: AccuracyCheck | null; onCheck(): void }; // an organisation's accuracy check
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
  const { platform, customerId, check, orgName, spent, catalogue, saved, defaults, env, maxAgents, draft, errors, busy, onChange } = props;
  const from = (field: Parameters<typeof inheritedText>[0]) => inheritedText(field, defaults, env, catalogue);
  // A scan setting's select: "" follows the platform default, or the server's on the platform.
  const setting = <T extends string>(field: ScanSetting, values: T[], words: Record<T, string>) => ({
    platform,
    inherit: platform ? `The server's level (${env[field]})` : `Platform default (${from(field)})`,
    value: draft[field] ?? "",
    options: values.map((value) => ({ value, words: words[value] })),
    onPick: (value: string) => onChange({ ...draft, [field]: value || null }),
  });
  const section = { platform, draft, busy, onChange };
  const rupees = inrText(parseBudget(draft.budget));
  const scanTicked = catalogue.filter((model) => draft.scan_models.includes(model.id));
  const scanNoLevels = scanTicked.length > 0 && scanTicked.every(hasNoLevels);
  return (
    <>
      <SectionBox title="Scanning" section="scan" {...section}>
        <div className={styles.scanLayout}>
          <div className={styles.scanGrid}>
            <div className={styles.scanFields}>
              <h3 className={styles.subHeading}>First read</h3>
              <ModelChoices
                area="scan"
                title="Models its scans may be read with"
                none={platform ? `None ticked: the server's model (${env.scan_model}).` : `None ticked: from platform defaults, ${from("scan_models")}.`}
                models={offered(catalogue, "scan", saved.scan_models)}
                draft={draft}
                errors={errors}
                onChange={onChange}
              />
              {scanNoLevels ? (
                // Every ticked scan model has no thinking levels: nothing to choose (Tushar, 18 Oct 2026).
                <div className="field">
                  <span className="label">Thinking level</span>
                  <p className="hint">{NO_LEVELS}: the ticked models have one mode.</p>
                </div>
              ) : (
                <SettingSelect id="ai-scan-thinking" label="Thinking level" {...setting("scan_thinking", SCAN_LEVELS, LEVEL_WORDS)} />
              )}
              <SettingSelect
                id="ai-scan-resolution"
                label="Picture detail"
                hint={RESOLUTION_HINT}
                {...setting("scan_resolution", RESOLUTIONS, RESOLUTION_WORDS)}
              />
              <h3 className={styles.subHeading}>Pages read again</h3>
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
                <SettingSelect
                  id="ai-dynamic"
                  label={COST_SAVING}
                  platform={false}
                  hint={DYNAMIC_HINT}
                  inherit={`Platform default (${from("dynamic")})`}
                  value={draft.dynamic === null ? "" : draft.dynamic ? "on" : "off"}
                  options={[
                    { value: "on", words: "On" },
                    { value: "off", words: "Off" },
                  ]}
                  onPick={(value) => onChange({ ...draft, dynamic: value === "" ? null : value === "on" })}
                />
              )}
              {platform && (
                <span id="ai-dynamic-hint" className="hint">
                  {DYNAMIC_HINT}
                </span>
              )}
              <SettingSelect
                id="ai-careful-thinking"
                label="Thinking for pages read again"
                hint={CAREFUL_THINKING_HINT}
                {...setting("careful_thinking", SCAN_LEVELS, LEVEL_WORDS)}
              />
              <SettingSelect
                id="ai-careful-resolution"
                label="Picture detail for pages read again"
                hint={CAREFUL_RESOLUTION_HINT}
                {...setting("careful_resolution", RESOLUTIONS, RESOLUTION_WORDS)}
              />
            </div>
            <CostPanel platform={platform} customerId={customerId} saved={saved} draft={draft} check={check} />
          </div>
        </div>
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

/** One scan setting's select (#463): its label, whether the value is the organisation's own
 * (set here, on the platform) or followed, its hint, and a way back to the followed value.
 * "" is the followed value. */
function SettingSelect(props: {
  id: string;
  label: string;
  platform: boolean;
  inherit: string;
  value: string;
  options: { value: string; words: string }[];
  hint?: string;
  onPick(value: string): void;
}) {
  const { id, label, platform, inherit, value, options, hint, onPick } = props;
  const own = value !== "";
  const back = platform ? "Use the server's setting" : "Use platform default";
  return (
    <div className="field">
      <div className={styles.fieldHead}>
        <label htmlFor={id} className="label">
          {label}
        </label>
        <span id={`${id}-origin`} className={styles.origin}>
          {own ? (platform ? "Set here" : "Own setting") : platform ? "The server's setting" : "Platform default"}
        </span>
      </div>
      <select
        id={id}
        className="input"
        value={value}
        aria-describedby={hint ? `${id}-origin ${id}-hint` : `${id}-origin`}
        onChange={(event) => onPick(event.target.value)}
      >
        <option value="">{inherit}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.words}
          </option>
        ))}
      </select>
      {hint && (
        <span id={`${id}-hint`} className="hint">
          {hint}
        </span>
      )}
      {own && (
        <div>
          <button
            type="button"
            className="btn btn-quiet"
            aria-label={`${back}: ${label}`}
            onClick={() => {
              // The button goes once the value follows: the focus moves to its select.
              document.getElementById(id)?.focus();
              onPick("");
            }}
          >
            {back}
          </button>
        </div>
      )}
    </div>
  );
}

/** The Cost per page panel (#463): the measured cost a page at the draft's scan settings,
 * the change from what is saved while they differ, the pages read cleanly and, for an
 * organisation, its accuracy against max settings. Never an empty box. */
function CostPanel(props: {
  platform: boolean;
  customerId: string | null;
  saved: AiValues;
  draft: AiDraft;
  check?: { latest: AccuracyCheck | null; onCheck(): void };
}) {
  const { platform, customerId, saved, draft, check } = props;
  const { scan_thinking, scan_resolution, careful_thinking, careful_resolution } = draft;
  const differs = !sameSettings(saved, draft);
  const finished = check?.latest?.finished_at ?? null; // a finished check: ask again for its result
  const [savedCost, setSavedCost] = useState<CostReply>(null);
  const [draftCost, setDraftCost] = useState<CostReply>(null);
  // What is saved: asked when the dialog opens and again after a save (saved changes).
  useEffect(() => askScanCost(costAsk(customerId, saved), postScanCost, setSavedCost), [customerId, saved, finished]);
  // The draft, only while it differs: asked once it rests, the last reply winning.
  useEffect(
    () =>
      differs
        ? askScanCost(costAsk(customerId, { scan_thinking, scan_resolution, careful_thinking, careful_resolution }), postScanCost, setDraftCost)
        : undefined,
    [customerId, differs, finished, scan_thinking, scan_resolution, careful_thinking, careful_resolution],
  );
  const shown = differs ? draftCost : savedCost;
  const words = costWords(shown);
  const change = differs ? costChange(savedCost, draftCost) : null;
  const reply = shown !== null && shown !== "failed" ? shown : null;
  const accuracy = check ? accuracyWords(reply?.accuracy ?? null, check.latest) : null;
  const running = checkRunning(check?.latest ?? null);
  return (
    <div className={styles.costColumn}>
      <section className={styles.costPanel} aria-labelledby="ai-cost-heading">
        <h3 id="ai-cost-heading" className={styles.subHeading}>
          Cost per page
        </h3>
        <div id="ai-scan-cost" aria-live="polite">
          {words.figure && <p className={styles.costFigure}>{words.figure}</p>}
          {words.dollars && <p className={`${styles.costSmall} tabular`}>{words.dollars}</p>}
          {change && (
            <p className={`${styles.costChange} tabular`}>
              Now {change.from} <span aria-hidden="true">→</span>
              <span className="visually-hidden">to</span> {change.to} a page: <strong>{change.verdict}</strong>
            </p>
          )}
          {words.lines.map((line) => (
            <p key={line} className={styles.costSmall}>
              {line}
            </p>
          ))}
        </div>
        <div className={styles.accuracy}>
          <h3 id={ACCURACY_ID} className={styles.subHeading} tabIndex={-1}>
            Accuracy
          </h3>
          <div id="ai-accuracy" aria-live="polite">
            {reply && <p className={styles.costSmall}>{cleanText(reply)}</p>}
            {accuracy?.figure && <p className={styles.costFigure}>{accuracy.figure}</p>}
            {accuracy?.line && <p className={styles.costSmall}>{accuracy.line}</p>}
          </div>
          <p className="hint">{CLEAN_HINT}</p>
          {platform && <p className="hint">{CHECK_ON_ORG}</p>}
          {check && !running && (
            <div>
              <button id="ai-check-accuracy" type="button" className="btn" onClick={check.onCheck}>
                {CHECK_LABEL}
              </button>
            </div>
          )}
        </div>
      </section>
      <p className="hint">{APPLIES_NOTE}</p>
    </div>
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
                {hasNoLevels(model) && <span className={styles.statusNote}>{NO_LEVELS}</span>}
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
