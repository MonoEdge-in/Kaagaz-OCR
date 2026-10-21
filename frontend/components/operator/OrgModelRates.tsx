"use client";

import { useCallback, useEffect, useState } from "react";
import { CodeField } from "@/app/login/CodeField";
import { normalizeCode } from "@/app/login/twoStep";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { ApiError, getOrgModelRates, putOrgModelRate, putUserModelRate } from "@/lib/api";
import type { OrgModelRates, Organisation } from "@/lib/types";
import { canCloseDialog, operatorErrorMessage } from "./logic";
import styles from "./Operator.module.css";

export const orgModelRateFocusId = (id: string) => `org-model-rate-${id}`;

export function OrgModelRates({ org, onClose, onSaved }: { org: Organisation; onClose(): void; onSaved(message: string): void }) {
  const [rates, setRates] = useState<OrgModelRates | null>(null);
  const [orgRpm, setOrgRpm] = useState("");
  const [users, setUsers] = useState<Record<string, string>>({});
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const focusFirst = useCallback((form: HTMLFormElement | null) => {
    form?.querySelector<HTMLElement>("input, button")?.focus();
  }, []);

  useEffect(() => {
    let cancelled = false;
    getOrgModelRates(org.id).then(
      (reply) => {
        if (cancelled) return;
        setRates(reply);
        setOrgRpm(reply.organisation_rpm === null ? "" : String(reply.organisation_rpm));
        setUsers(Object.fromEntries(reply.users.map((user) => [user.id, String(user.rpm ?? "")])));
      },
      (caught: unknown) => !cancelled && setProblem(operatorErrorMessage(caught)),
    );
    return () => {
      cancelled = true;
    };
  }, [org.id, attempt]);

  async function save(update: () => Promise<OrgModelRates>, message: string) {
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      const result = await update();
      setRates(result);
      setOrgRpm(result.organisation_rpm === null ? "" : String(result.organisation_rpm));
      setUsers(Object.fromEntries(result.users.map((user) => [user.id, String(user.rpm ?? "")])));
      setCode("");
      onSaved(message);
    } catch (caught) {
      setProblem(caught instanceof ApiError && caught.code === "step_up_failed" ? "Enter a fresh two-step code." : operatorErrorMessage(caught));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ActionDialog title={`OpenRouter request limits · ${org.name}`} onClose={onClose} canClose={(way) => canCloseDialog({ busy }, way)} onClosed={() => document.getElementById(orgModelRateFocusId(org.id))?.focus()}>
      {!rates && !problem && <p className="muted" role="status">Loading request limits…</p>}
      {problem && <p className="error-text" role="alert">{problem}</p>}
      {problem && !rates && <button type="button" className="btn" onClick={() => { setProblem(null); setAttempt((value) => value + 1); }}>Try again</button>}
      {rates && (
        <form ref={focusFirst} className={styles.dialogForm} onSubmit={(event) => event.preventDefault()}>
          <p>One request is one OpenRouter API call. The platform shares a maximum of 1,200 requests per minute across every API and worker. Organisation caps are optional (1–600 RPM); blank uses fair sharing. User overrides are optional (1–120 RPM) and are also bounded by their organisation cap.</p>
          <label htmlFor="org-model-rpm">Organisation cap (RPM; blank uses fair sharing)</label>
          <input id="org-model-rpm" className="input" type="number" min={1} max={600} value={orgRpm} onChange={(event) => setOrgRpm(event.target.value)} />
          <button type="button" className="btn" disabled={busy || (orgRpm !== "" && (!Number.isInteger(Number(orgRpm)) || Number(orgRpm) < 1 || Number(orgRpm) > 600))} onClick={() => void save(() => putOrgModelRate(org.id, orgRpm === "" ? null : Number(orgRpm), normalizeCode(code)), orgRpm === "" ? `Cleared the OpenRouter cap for ${org.name}.` : `Saved the OpenRouter request limit for ${org.name}.`)}>{orgRpm === "" ? "Use fair sharing" : "Save organisation cap"}</button>
          <h3>Per-user overrides</h3>
          {rates.users.length === 0 && <p>No users in this organisation.</p>}
          {rates.users.map((user) => (
            <div key={user.id} className={styles.rateUser}>
              <label htmlFor={`user-rpm-${user.id}`}>{user.email} (1–120 RPM; blank inherits organisation)</label>
              <input id={`user-rpm-${user.id}`} className="input" type="number" min={1} max={120} value={users[user.id] ?? ""} onChange={(event) => setUsers({ ...users, [user.id]: event.target.value })} />
              <button type="button" className="btn" disabled={busy || (users[user.id] !== "" && (!Number.isInteger(Number(users[user.id])) || Number(users[user.id]) < 1 || Number(users[user.id]) > 120))} onClick={() => void save(() => putUserModelRate(org.id, user.id, users[user.id] === "" ? null : Number(users[user.id]), normalizeCode(code)), `Saved the OpenRouter request limit for ${user.email}.`)}>{users[user.id] === "" ? "Use organisation limit" : "Save user limit"}</button>
            </div>
          ))}
          <CodeField id="model-rate-code" label="Fresh two-step code" value={code} onChange={setCode} error={null} />
          {problem && <p className="error-text" role="alert">{problem}</p>}
        </form>
      )}
    </ActionDialog>
  );
}
