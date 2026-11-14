"use client";

// Admin, Settings, WhatsApp (#603, PRD 7 section 15): off by default. "Turn on WhatsApp…" opens the
// one dialog (what people can do, Allow voice notes ticked, Allow saving files not, answers already
// sent stay on the phone, and the tick "our data rules allow WhatsApp") and asks for the Admin's password; the server audits it and emails
// Management and the other Admins. Once on: the roles that may connect (Members and Reviewers by
// default; Admins and Management are extra ticks), voice notes and uploads, saved by Save; Turn off
// asks first (everyone is disconnected). While the super admin has it off, a line says so. Shown
// only while the server has WhatsApp on (Me's ``whatsapp``); its own form, apart from Settings'.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import libraryStyles from "@/components/documents/Library.module.css";
import { LoadFailed } from "@/components/operator/LoadFailed";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PasswordField } from "@/components/ui/PasswordField";
import { ApiError, adminWhatsApp, saveAdminWhatsApp } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { WhatsAppAdmin } from "@/lib/types";
import {
  MANAGEMENT_HINT,
  MANAGEMENT_LABEL,
  NOT_AVAILABLE,
  ROLE_TICKS,
  RULES_LABEL,
  RULES_MISSING,
  SENT_STAYS,
  TURN_OFF_TEXT,
  TURN_ON_TEXT,
  UPLOADS_LABEL,
  VOICE_LABEL,
  formChanged,
  formOf,
  turnOnTitle,
  withRole,
} from "./whatsappSettings";
import type { WhatsAppForm } from "./whatsappSettings";
import styles from "./Admin.module.css";

function said(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : errorWords(caught);
}

export function WhatsAppPanel({ organisation }: { organisation: string }) {
  const [saved, setSaved] = useState<WhatsAppAdmin | null>(null);
  const [form, setForm] = useState<WhatsAppForm | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [dialog, setDialog] = useState<"on" | "off" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    adminWhatsApp().then(
      (reply) => {
        if (!live) return;
        setSaved(reply);
        setForm(formOf(reply));
      },
      (caught: unknown) => live && setLoadError(errorWords(caught)),
    );
    return () => {
      live = false;
    };
  }, [attempt]);

  async function save(next: WhatsAppForm, password?: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const reply = await saveAdminWhatsApp(next, password);
      setSaved(reply);
      setForm(formOf(reply));
      setDialog(null);
      setNotice("Saved.");
    } catch (caught) {
      setError(said(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card stack" aria-labelledby="whatsapp-heading">
      <h2 id="whatsapp-heading" className={styles.sectionHeading}>
        WhatsApp
      </h2>
      {loadError && (
        <LoadFailed
          what="WhatsApp's settings"
          message={loadError}
          onRetry={() => {
            setLoadError(null);
            setAttempt((n) => n + 1);
          }}
        />
      )}
      {saved && form && !saved.enabled && (
        <div className="stack">
          <p className="hint">{saved.available ? TURN_ON_TEXT : NOT_AVAILABLE}</p>
          {saved.available && (
            <button type="button" className="btn btn-primary" onClick={() => setDialog("on")}>
              Turn on WhatsApp…
            </button>
          )}
        </div>
      )}
      {saved && form && saved.enabled && (
        <form
          className="stack"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            if (!busy) void save(form);
          }}
        >
          <fieldset className={styles.settingsGroup}>
            <legend className={styles.settingsLegend}>Who may connect</legend>
            {ROLE_TICKS.map((tick) => (
              <label key={tick.role} className={libraryStyles.check}>
                <input
                  type="checkbox"
                  checked={form.roles.includes(tick.role)}
                  onChange={(event) => setForm(withRole(form, tick.role, event.target.checked))}
                />
                {tick.label}
                {tick.hint && <span className="hint"> {tick.hint}</span>}
              </label>
            ))}
            <label className={libraryStyles.check}>
              <input
                type="checkbox"
                checked={form.management}
                onChange={(event) => setForm({ ...form, management: event.target.checked })}
              />
              {MANAGEMENT_LABEL}
              <span className="hint"> {MANAGEMENT_HINT}</span>
            </label>
          </fieldset>
          <fieldset className={styles.settingsGroup}>
            <legend className={styles.settingsLegend}>On WhatsApp</legend>
            <label className={libraryStyles.check}>
              <input type="checkbox" checked={form.voice} onChange={(event) => setForm({ ...form, voice: event.target.checked })} />
              {VOICE_LABEL}
            </label>
            <label className={libraryStyles.check}>
              <input
                type="checkbox"
                checked={form.uploads}
                onChange={(event) => setForm({ ...form, uploads: event.target.checked })}
              />
              {UPLOADS_LABEL}
            </label>
          </fieldset>
          <p className="hint">{SENT_STAYS}</p>
          {error && !dialog && (
            <p className="error-text" role="alert">
              {error}
            </p>
          )}
          <div className="actions">
            {formChanged(saved, form) && (
              <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
                Save
              </button>
            )}
            <button type="button" className="btn" onClick={() => setDialog("off")}>
              Turn off WhatsApp
            </button>
          </div>
        </form>
      )}
      <span aria-live="polite" className={styles.saved}>
        {notice ?? ""}
      </span>
      {dialog === "on" && form && (
        <TurnOn
          organisation={organisation}
          form={form}
          busy={busy}
          error={error}
          onCancel={() => {
            setDialog(null);
            setError(null);
          }}
          onTurnOn={(next, password) => void save(next, password)}
        />
      )}
      {form && (
        <ConfirmDialog
          open={dialog === "off"}
          title="Turn off WhatsApp?"
          confirmLabel="Turn off"
          busy={busy}
          error={dialog === "off" ? error : null}
          onConfirm={() => void save({ ...form, enabled: false })}
          onCancel={() => setDialog(null)}
        >
          <p>{TURN_OFF_TEXT}</p>
        </ConfirmDialog>
      )}
    </section>
  );
}

/** The one dialog (PRD 7 section 15), with the Admin's password. */
function TurnOn({
  organisation,
  form,
  busy,
  error,
  onCancel,
  onTurnOn,
}: {
  organisation: string;
  form: WhatsAppForm;
  busy: boolean;
  error: string | null;
  onCancel(): void;
  onTurnOn(next: WhatsAppForm, password: string): void;
}) {
  const [voice, setVoice] = useState(true);
  const [uploads, setUploads] = useState(false);
  const [rules, setRules] = useState(false);
  const [password, setPassword] = useState("");
  const [missing, setMissing] = useState<{ rules: boolean; password: boolean }>({ rules: false, password: false });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMissing({ rules: !rules, password: password === "" });
    if (rules && password !== "") onTurnOn({ ...form, enabled: true, voice, uploads }, password);
  }

  return (
    <ActionDialog title={turnOnTitle(organisation)} onClose={onCancel} canClose={() => !busy}>
      <form className="stack" method="post" noValidate onSubmit={submit}>
        <p>{TURN_ON_TEXT}</p>
        <label className={libraryStyles.check}>
          <input type="checkbox" checked={voice} onChange={(event) => setVoice(event.target.checked)} />
          {VOICE_LABEL}
        </label>
        <label className={libraryStyles.check}>
          <input type="checkbox" checked={uploads} onChange={(event) => setUploads(event.target.checked)} />
          {UPLOADS_LABEL}
        </label>
        <p>{SENT_STAYS}</p>
        <label className={libraryStyles.check}>
          <input type="checkbox" checked={rules} onChange={(event) => setRules(event.target.checked)} />
          {RULES_LABEL}
        </label>
        {missing.rules && (
          <span className="error-text" role="alert">
            {RULES_MISSING}
          </span>
        )}
        <PasswordField
          id="whatsapp-admin-password"
          label="Your Kaagaz password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
          required
          error={missing.password ? "Enter your password." : null}
        />
        {error && (
          <p className="error-text" role="alert">
            {error}
          </p>
        )}
        <div className="actions">
          <button type="button" className="btn" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
            Turn on
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
