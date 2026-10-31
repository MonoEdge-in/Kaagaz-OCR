"use client";

// This customer's settings: the largest file one upload may be (empty means the
// platform limit applies; the platform limit is the ceiling), and auto-filing
// (feature 28), shown only once the platform has allowed it, saved by the same Save.
// UI audit AD6 (16 Oct 2026): each in its own section (Uploads, Filing), and the Save
// bar sticks to the foot of the screen only while something is changed.
// #457 phase 1: "Approve new sign-in connections automatically", saved at once (turning it on
// asks first, with the warning; "Saved." after each change), shown only while a provider is on
// (the settings reply leaves it out otherwise).
// #468: unsaved changes are kept in this tab for the same admin (lib/drafts.ts) and come back over
// the saved settings; onUnsaved tells AdminView, which asks before another tab opens.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import libraryStyles from "@/components/documents/Library.module.css";
import { LoadFailed } from "@/components/operator/LoadFailed";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ApiError, getSettings, setAutoApprove, updateSettings } from "@/lib/api";
import { clearDraft, readDraft, saveDraftSoon } from "@/lib/drafts";
import { errorWords } from "@/lib/errorWords";
import { useSession } from "@/lib/session";
import type { CustomerSettings } from "@/lib/types";
import {
  AUTO_APPROVE_LABEL,
  AUTO_APPROVE_NOTE,
  AUTO_APPROVE_SAVED,
  autoApproveWarning,
  AUTOFILE_HELP,
  AUTOFILE_LABEL,
  SETTINGS_DRAFT,
  parseUploadLimit,
  settingsChanged,
  settingsDraft,
  settingsErrorText,
  settingsFromDraft,
} from "./logic";
import styles from "./Admin.module.css";

/** providers: the sign-in providers switched on (Me's), for the auto-approval warning. */
export function SettingsPanel({
  providers,
  onUnsaved,
}: {
  providers: readonly string[];
  onUnsaved?(unsaved: boolean): void;
}) {
  const userId = useSession().me.user.id;
  const [settings, setSettings] = useState<CustomerSettings | null>(null);
  const [text, setText] = useState("");
  const [autofileOn, setAutofileOn] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    getSettings().then(
      (result) => {
        if (cancelled) return;
        setSettings(result);
        const form = settingsFromDraft(readDraft(userId, SETTINGS_DRAFT), result);
        setText(form.text);
        setAutofileOn(form.autofileOn);
      },
      (caught: unknown) => {
        if (!cancelled) {
          setLoadError(
            caught instanceof ApiError
              ? errorWords(caught)
              : "Check your internet connection, then try again.",
          );
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt, userId]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!settings || busy) return; // never a second save while one runs
    setSaved(null);
    const parsed = parseUploadLimit(text, settings.platform_max_upload_mb);
    if (!parsed.ok) {
      setError(parsed.message);
      document.getElementById("upload-limit")?.focus();
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const result = await updateSettings(parsed.value, settings.autofile.granted ? autofileOn : undefined);
      setSettings(result);
      setText(result.max_upload_mb === null ? "" : String(result.max_upload_mb));
      setAutofileOn(result.autofile.on);
      clearDraft(userId, SETTINGS_DRAFT);
      setSaved("Saved.");
    } catch (caught) {
      setError(settingsErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const changed = settings !== null && settingsChanged(settings, text, autofileOn);

  useEffect(() => {
    if (!settings) return; // not loaded yet: a kept draft is still to be read
    saveDraftSoon(userId, SETTINGS_DRAFT, settingsDraft(settings, text, autofileOn));
  }, [userId, settings, text, autofileOn]);

  useEffect(() => onUnsaved?.(changed), [changed, onUnsaved]);

  return (
    <section className="card stack" aria-labelledby="settings-heading">
      <h2 id="settings-heading" className={styles.sectionHeading}>
        Settings
      </h2>
      {loadError && (
        <LoadFailed
          what="the settings"
          message={loadError}
          onRetry={() => {
            setLoadError(null);
            setAttempt((n) => n + 1);
          }}
        />
      )}
      {!settings && !loadError && (
        <>
          <p className="visually-hidden" role="status">
            Loading the settings…
          </p>
          <span className={`delayed skeleton ${styles.skeletonRow}`} aria-hidden="true" />
        </>
      )}
      {settings && (
        <form className="stack" onSubmit={submit} noValidate>
          <p className={styles.customer}>
            Workspace: <strong>{settings.customer_name}</strong>
          </p>
          <fieldset className={styles.settingsGroup}>
            <legend className={styles.settingsLegend}>Uploads</legend>
            <div className="field">
              <label htmlFor="upload-limit">Largest file one upload may be, in MB</label>
              <input
                id="upload-limit"
                className={`input ${styles.narrow}`}
                type="text"
                inputMode="numeric"
                value={text}
                onChange={(event) => {
                  setText(event.target.value);
                  setSaved(null); // "Saved." is about the old value
                }}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "upload-limit-hint upload-limit-error" : "upload-limit-hint"}
              />
              <span id="upload-limit-hint" className="hint">
                The platform limit is {settings.platform_max_upload_mb} MB. Leave the box empty to
                use it.
              </span>
              {error && (
                <span id="upload-limit-error" className="error-text" role="alert">
                  {error}
                </span>
              )}
            </div>
          </fieldset>
          <AutoApproveSwitch settings={settings} providers={providers} onSaved={setSettings} />
          {settings.autofile.granted && (
            <fieldset className={styles.settingsGroup}>
              <legend className={styles.settingsLegend}>Filing</legend>
              <AutofileSwitch granted on={autofileOn} onChange={setAutofileOn} />
            </fieldset>
          )}
          {changed && (
            <div className={`row ${styles.saveBar}`}>
              <span className="muted">You have changes that are not saved.</span>
              <button
                type="submit"
                className={`btn btn-primary ${busy ? styles.busy : ""}`}
                disabled={busy}
                aria-busy={busy || undefined}
              >
                {busy && <span className="spinner" aria-hidden="true" />}
                Save
              </button>
            </div>
          )}
          <span aria-live="polite" className={styles.saved}>
            {saved ?? ""}
          </span>
        </form>
      )}
    </section>
  );
}

/** "Approve new sign-in connections automatically" (#457 phase 1): saved at once, apart from the
 * form's Save. Turning it on asks first with the warning; nothing shows while no provider is on. */
function AutoApproveSwitch({
  settings,
  providers,
  onSaved,
}: {
  settings: CustomerSettings;
  providers: readonly string[];
  onSaved(next: CustomerSettings): void;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const warning = autoApproveWarning(providers);

  async function save(on: boolean) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      onSaved(await setAutoApprove(on));
      setAsking(false);
      setSaved(true);
    } catch (caught) {
      setError(settingsErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  if (settings.auto_approve_connections === undefined) return null;
  return (
    <fieldset className={styles.settingsGroup}>
      <legend className={styles.settingsLegend}>Sign-in</legend>
      <div className="field">
        <label className={libraryStyles.check}>
          <input
            type="checkbox"
            checked={settings.auto_approve_connections}
            disabled={busy}
            onChange={(event) => (event.target.checked ? setAsking(true) : void save(false))}
            aria-describedby="auto-approve-hint"
          />
          {AUTO_APPROVE_LABEL}
        </label>
        <span id="auto-approve-hint" className="hint">
          {warning} {AUTO_APPROVE_NOTE}
        </span>
        <span className="hint" role="status">
          {saved ? AUTO_APPROVE_SAVED : null}
        </span>
        {error && !asking && (
          <span className="error-text" role="alert">
            {error}
          </span>
        )}
      </div>
      <ConfirmDialog
        open={asking}
        title="Approve new connections automatically?"
        confirmLabel="Turn on"
        busy={busy}
        error={asking ? error : null}
        onConfirm={() => void save(true)}
        onCancel={() => setAsking(false)}
      >
        <p>{warning}</p>
      </ConfirmDialog>
    </fieldset>
  );
}

/** Auto-filing's checkbox (feature 28), saved by the form's one Save; nothing until the platform allows it. */
export function AutofileSwitch({ granted, on, onChange }: { granted: boolean; on: boolean; onChange(on: boolean): void }) {
  if (!granted) return null;
  return (
    <div className="field">
      <label className={libraryStyles.check}>
        <input
          type="checkbox"
          checked={on}
          onChange={(event) => onChange(event.target.checked)}
          aria-describedby="autofile-hint"
        />
        {AUTOFILE_LABEL}
      </label>
      <span id="autofile-hint" className="hint">
        {AUTOFILE_HELP}
      </span>
    </div>
  );
}
