"use client";

// AI settings by task (spec 17 Oct 2026): one row per task (Scanning, Ask, Ensemble,
// Auto-filing), a segmented control for how much effort it gets, the chosen level's hint
// under it. scope "organisation" (Admin, AI settings tab): the organisation's defaults and
// "Members can change this" (not locked). scope "member" (/settings/ai, from the account
// menu): a locked task shows its value and "Set by your admin."; an open one can use the
// organisation's setting or the member's own. A choice above the organisation's limit is
// disabled with a note. A task switched off for the whole product says so. The Save bar
// shows only while something is changed (as Settings). Never a model name or cost word.

import { useEffect, useId, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import libraryStyles from "@/components/documents/Library.module.css";
import reviewStyles from "@/components/review/Review.module.css";
import { ApiError, FEATURE_OFF, getAiSettings, putMyAiSettings, putOrgAiSettings } from "@/lib/api";
import {
  CHOICES,
  HINTS,
  LEVEL_NAMES,
  MEMBERS_CAN_CHANGE,
  NOT_ALLOWED,
  SET_BY_ADMIN,
  TASK_NAMES,
  USE_ORGANISATION,
  aiSaveErrorText,
  changedOnly,
  mineDraftOf,
  orgDraftOf,
} from "@/lib/aiSettings";
import type { MineDraft, OrgDraft } from "@/lib/aiSettings";
import type { TaskLevel, AiSettings, AiTaskSetting } from "@/lib/types";
import { Toast } from "./Toast";
import type { Notice } from "./Toast";
import styles from "./Admin.module.css";

export type AiScope = "organisation" | "member";
type Draft = OrgDraft | MineDraft;

const draftOf = (scope: AiScope, settings: AiSettings): Draft =>
  scope === "organisation" ? orgDraftOf(settings) : mineDraftOf(settings);

export function AiSettingsPanel({ scope }: { scope: AiScope }) {
  const [settings, setSettings] = useState<AiSettings | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAiSettings().then(
      (result) => {
        if (cancelled) return;
        setSettings(result);
        setDraft(draftOf(scope, result));
      },
      (caught: unknown) =>
        !cancelled &&
        setLoadError(caught instanceof ApiError ? caught.message : "Could not load the AI settings. Check your connection, then try again."),
    );
    return () => {
      cancelled = true;
    };
  }, [scope]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!settings || busy) return; // never a second save while one runs
    const changes = changedOnly<unknown>(draftOf(scope, settings), draft);
    setBusy(true);
    setError(null);
    try {
      const result =
        scope === "organisation" ? await putOrgAiSettings(changes as OrgDraft) : await putMyAiSettings(changes as MineDraft);
      setSettings(result);
      setDraft(draftOf(scope, result));
      setNotice({ text: "Saved." });
    } catch (caught) {
      setError(aiSaveErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack" aria-labelledby="ai-settings-heading">
      <h2 id="ai-settings-heading" className={styles.sectionHeading}>
        AI settings
      </h2>
      {loadError && (
        <p className="error-text" role="alert">
          {loadError}
        </p>
      )}
      {!settings && !loadError && (
        <>
          <p className="visually-hidden" role="status">
            Loading the AI settings…
          </p>
          <span className={`delayed skeleton ${styles.skeletonRow}`} aria-hidden="true" />
        </>
      )}
      {settings && (
        <AiSettingsForm
          scope={scope}
          settings={settings}
          draft={draft}
          busy={busy}
          error={error}
          onChange={setDraft}
          onSubmit={submit}
        />
      )}
      <Toast notice={notice} onClose={() => setNotice(null)} />
    </section>
  );
}

/** The rows and the Save bar, from what is saved and the draft (rendered in tests). */
export function AiSettingsForm(props: {
  scope: AiScope;
  settings: AiSettings;
  draft: Draft;
  busy: boolean;
  error: string | null;
  onChange(draft: Draft): void;
  onSubmit(event: FormEvent<HTMLFormElement>): void;
}) {
  const { scope, settings, draft, busy, error, onChange, onSubmit } = props;
  const changed = Object.keys(changedOnly<unknown>(draftOf(scope, settings), draft)).length > 0;
  return (
    <form className="stack" onSubmit={onSubmit} noValidate>
      <p className="muted">
        {scope === "organisation"
          ? "How much care each task gets in your organisation, and whether members can change it."
          : "How much care each task gets for you."}
      </p>
      {settings.tasks.map((task) =>
        scope === "organisation" ? (
          <OrgRow key={task.task} task={task} value={(draft as OrgDraft)[task.task]} onChange={(next) => onChange({ ...draft, [task.task]: next })} />
        ) : (
          <MemberRow key={task.task} task={task} mine={(draft as MineDraft)[task.task] ?? null} onChange={(next) => onChange({ ...draft, [task.task]: next })} />
        ),
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {changed && (
        <div className={`row ${styles.saveBar}`}>
          <span className="muted">You have changes that are not saved.</span>
          <button type="submit" className={`btn btn-primary ${busy ? styles.busy : ""}`} disabled={busy} aria-busy={busy || undefined}>
            {busy && <span className="spinner" aria-hidden="true" />}
            Save
          </button>
        </div>
      )}
    </form>
  );
}

function OrgRow({ task, value, onChange }: { task: AiTaskSetting; value?: { level: TaskLevel; locked: boolean }; onChange(next: { level: TaskLevel; locked: boolean }): void }) {
  const current = value ?? { level: task.organisation ?? task.effective, locked: task.locked };
  return (
    <TaskGroup task={task}>
      <Levels task={task} value={current.level} onPick={(level) => onChange({ ...current, level })} />
      <label className={libraryStyles.check}>
        <input type="checkbox" checked={!current.locked} onChange={(event) => onChange({ ...current, locked: !event.target.checked })} />
        {MEMBERS_CAN_CHANGE}
      </label>
    </TaskGroup>
  );
}

function MemberRow({ task, mine, onChange }: { task: AiTaskSetting; mine: TaskLevel | null; onChange(next: TaskLevel | null): void }) {
  const theirs = task.organisation ?? task.effective;
  if (task.locked) {
    return (
      <TaskGroup task={task}>
        <p className={styles.customer}>
          <strong>{LEVEL_NAMES[theirs]}</strong>
        </p>
        <p className="hint">{SET_BY_ADMIN}</p>
      </TaskGroup>
    );
  }
  return (
    <TaskGroup task={task}>
      <label className={libraryStyles.check}>
        <input type="checkbox" checked={mine === null} onChange={(event) => onChange(event.target.checked ? null : theirs)} />
        {USE_ORGANISATION}
      </label>
      <Levels task={task} value={mine ?? theirs} disabled={mine === null} onPick={onChange} />
    </TaskGroup>
  );
}

/** One task's fieldset; a task switched off for the whole product shows only the note. */
function TaskGroup({ task, children }: { task: AiTaskSetting; children: ReactNode }) {
  return (
    <fieldset className={styles.settingsGroup}>
      <legend className={styles.settingsLegend}>{TASK_NAMES[task.task]}</legend>
      {task.available ? children : <p className="muted">{FEATURE_OFF}</p>}
    </fieldset>
  );
}

/** The segmented control: one control tall, 2px corners; a level above the limit disabled with the note. */
function Levels({ task, value, disabled = false, onPick }: { task: AiTaskSetting; value: TaskLevel; disabled?: boolean; onPick(level: TaskLevel): void }) {
  const id = useId();
  const choices = CHOICES[task.task];
  const someNotAllowed = choices.some((level) => !task.allowed.includes(level));
  return (
    <>
      <div className={reviewStyles.segments} role="radiogroup" aria-label={`${TASK_NAMES[task.task]} level`}>
        {choices.map((level) => {
          const allowed = task.allowed.includes(level);
          return (
            <label key={level} className={reviewStyles.segment}>
              <input
                type="radio"
                name={`${id}-level`}
                value={level}
                checked={value === level}
                disabled={disabled || !allowed}
                aria-describedby={allowed ? `${id}-hint` : `${id}-hint ${id}-limit`}
                onChange={() => onPick(level)}
              />
              {LEVEL_NAMES[level]}
            </label>
          );
        })}
      </div>
      <p id={`${id}-hint`} className="hint">
        {HINTS[task.task][value]}
      </p>
      {someNotAllowed && (
        <p id={`${id}-limit`} className="hint">
          {choices.filter((level) => !task.allowed.includes(level)).map((level) => LEVEL_NAMES[level]).join(", ")}: {NOT_ALLOWED}
        </p>
      )}
    </>
  );
}
