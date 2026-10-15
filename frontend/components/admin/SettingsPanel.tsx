"use client";

// This customer's settings: for now, the largest file one upload may be.
// Empty means the platform limit applies; the platform limit is the ceiling.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, getSettings, updateSettings } from "@/lib/api";
import type { CustomerSettings } from "@/lib/types";
import { parseUploadLimit } from "./logic";
import styles from "./Admin.module.css";

export function SettingsPanel() {
  const [settings, setSettings] = useState<CustomerSettings | null>(null);
  const [text, setText] = useState("");
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
    if (!settings) return;
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
      const result = await updateSettings(parsed.value);
      setSettings(result);
      setText(result.max_upload_mb === null ? "" : String(result.max_upload_mb));
      setSaved("Saved.");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Could not save the settings. Check your connection, then try again.",
      );
    } finally {
      setBusy(false);
    }
  }

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
            Customer: <strong>{settings.customer_name}</strong>
          </p>
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
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? (
                <>
                  <span className="spinner" aria-hidden="true" />
                  Saving…
                </>
              ) : (
                "Save"
              )}
            </button>
            <span aria-live="polite" className={styles.saved}>
              {saved ?? ""}
            </span>
          </div>
        </form>
      )}
    </section>
  );
}
