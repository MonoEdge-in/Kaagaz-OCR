"use client";

// This customer's settings: the largest file one upload may be (empty means the
// platform limit applies; the platform limit is the ceiling), and auto-filing
// (feature 28), shown only once the platform has allowed it, saved by the same Save.
// UI audit AD6 (16 Oct 2026): each in its own section (Uploads, Filing), and the Save
// bar sticks to the foot of the screen only while something is changed.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import libraryStyles from "@/components/documents/Library.module.css";
import { ApiError, getSettings, updateSettings } from "@/lib/api";
import type { CustomerSettings } from "@/lib/types";
import { AUTOFILE_HELP, AUTOFILE_LABEL, parseUploadLimit, settingsChanged, settingsErrorText } from "./logic";
import styles from "./Admin.module.css";

export function SettingsPanel() {
  const [settings, setSettings] = useState<CustomerSettings | null>(null);
  const [text, setText] = useState("");
  const [autofileOn, setAutofileOn] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSettings().then(
      (result) => {
        if (cancelled) return;
        setSettings(result);
        setText(result.max_upload_mb === null ? "" : String(result.max_upload_mb));
        setAutofileOn(result.autofile.on);
      },
      (caught: unknown) => {
        if (!cancelled) {
          setLoadError(
            caught instanceof ApiError
              ? caught.message
              : "Could not load the settings. Check your connection, then try again.",
          );
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

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
      setSaved("Saved.");
    } catch (caught) {
      setError(settingsErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const changed = settings !== null && settingsChanged(settings, text, autofileOn);

  return (
    <section className="card stack" aria-labelledby="settings-heading">
      <h2 id="settings-heading" className={styles.sectionHeading}>
        Settings
      </h2>
      {loadError && (
        <p className="error-text" role="alert">
          {loadError}
        </p>
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
