"use client";

// "Move two-step to a new device" from the top bar (16 Oct 2026, a handover). Step 1:
// the password and a code from the current device; step 2: the new key as a QR code
// and as text, then a code from the new device; then done. Until step 2's code is
// accepted the current device keeps working. The key and token live only in this
// state. Never closes while busy; Esc, the backdrop or Close ask first when typed
// input or a shown key would be lost (twoStepMove.ts).

import { useCallback, useState } from "react";
import type { FormEvent } from "react";
import { CodeField } from "@/app/admin/login/CodeField";
import { codeError, groupSecret, normalizeCode, secretForCopy } from "@/app/admin/login/adminSignIn";
import enrol from "@/app/admin/login/adminLogin.module.css";
import { CopyButton } from "@/components/admin/CopyButton";
import { QrCode } from "@/components/admin/QrCode";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { operatorMoveConfirm, operatorMoveStart } from "@/lib/api";
import type { OperatorMoveOffer } from "@/lib/types";
import { MOVE_TEXT, closeAsked, hasUnsaved, leaveQuestion, moveErrorMessage, passwordError } from "./twoStepMove";
import type { MoveStep } from "./twoStepMove";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

const PASSWORD_ID = "move-password";
const CURRENT_ID = "move-current-code";
const NEW_ID = "move-new-code";

const focusOnMount = (element: HTMLElement | null) => element?.focus();

export function MoveTwoStepDialog({ onClose }: { onClose(): void }) {
  const [step, setStep] = useState<MoveStep>("check");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [offer, setOffer] = useState<OperatorMoveOffer | null>(null);
  const [problems, setProblems] = useState<{ password: string | null; code: string | null }>({ password: null, code: null });
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const focusKeep = useCallback((button: HTMLButtonElement | null) => button?.focus(), []);

  function askToClose() {
    const next = closeAsked({ busy, unsaved: hasUnsaved(step, { password, code }) });
    if (next === "ask") setLeaving(true);
    else if (next === "close") onClose();
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFailure(null);
    const found = { password: passwordError(password), code: codeError(code) };
    setProblems(found);
    if (found.password || found.code) {
      document.getElementById(found.password ? PASSWORD_ID : CURRENT_ID)?.focus();
      return;
    }
    setBusy(true);
    try {
      setOffer(await operatorMoveStart(password, normalizeCode(code)));
      setPassword("");
      setCode("");
      setStep("scan");
    } catch (caught) {
      setFailure(moveErrorMessage(caught, "check"));
      setCode(""); // a code works once and changes every 30 seconds
    }
    setBusy(false);
  }

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !offer) return;
    setFailure(null);
    const found = codeError(code);
    setProblems({ password: null, code: found });
    if (found) {
      document.getElementById(NEW_ID)?.focus();
      return;
    }
    setBusy(true);
    try {
      await operatorMoveConfirm(offer.move_token, normalizeCode(code));
      setOffer(null);
      setStep("done");
    } catch (caught) {
      setFailure(moveErrorMessage(caught, "scan"));
    }
    setCode("");
    setBusy(false);
  }

  const failed = failure && (
    <p className="error-text" role="alert">
      {failure}
    </p>
  );
  const leaveAsk = leaving && (
    <div className={styles.leaveAsk} role="alert">
      <p>{leaveQuestion(step)}</p>
      <div className={styles.buttons}>
        <button type="button" className="btn" onClick={onClose}>
          {MOVE_TEXT.closeAnyway}
        </button>
        <button ref={focusKeep} type="button" className="btn" onClick={() => setLeaving(false)}>
          {MOVE_TEXT.keepGoing}
        </button>
      </div>
    </div>
  );
  const buttons = (primary: string) => (
    <div className={styles.buttons}>
      <button type="button" className="btn" onClick={askToClose} disabled={busy}>
        {MOVE_TEXT.cancel}
      </button>
      <button type="submit" className={`btn btn-primary ${busy ? admin.busy : ""}`} disabled={busy} aria-busy={busy || undefined}>
        {busy && <span className="spinner" aria-hidden="true" />}
        {primary}
      </button>
    </div>
  );

  return (
    <ActionDialog title={MOVE_TEXT.title} onClose={askToClose} canClose={() => !busy}>
      {step === "check" && (
        <form className={styles.dialogForm} onSubmit={start} noValidate>
          <p>{MOVE_TEXT.intro}</p>
          <div className="field">
            <label htmlFor={PASSWORD_ID}>{MOVE_TEXT.password}</label>
            <input
              id={PASSWORD_ID}
              className="input"
              type="password"
              autoComplete="current-password"
              data-autofocus
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-invalid={problems.password ? true : undefined}
              aria-describedby={problems.password ? `${PASSWORD_ID}-error` : undefined}
            />
            {problems.password && (
              <span id={`${PASSWORD_ID}-error`} className="error-text">
                {problems.password}
              </span>
            )}
          </div>
          <CodeField
            id={CURRENT_ID}
            label={MOVE_TEXT.currentCode}
            value={code}
            onChange={setCode}
            error={problems.code}
            hint={MOVE_TEXT.currentHint}
          />
          {failed}
          {leaveAsk}
          {buttons(MOVE_TEXT.showKey)}
        </form>
      )}
      {step === "scan" && offer && (
        <form className={styles.dialogForm} onSubmit={confirm} noValidate aria-labelledby="move-scan-title">
          <h3 id="move-scan-title" ref={focusOnMount} tabIndex={-1} className={styles.resultHeading}>
            {MOVE_TEXT.scanTitle}
          </h3>
          <ol className={enrol.steps}>
            {MOVE_TEXT.scanSteps.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ol>
          <div className={`card ${enrol.keyCard}`}>
            <div className={enrol.qr}>
              <QrCode value={offer.otpauth_uri} label={MOVE_TEXT.qrLabel} />
            </div>
            <p className={enrol.note}>{MOVE_TEXT.noCamera}</p>
            <dl className={enrol.details}>
              <div className={enrol.detail}>
                <dt>Key</dt>
                <dd>
                  <code className={enrol.key} translate="no">
                    {groupSecret(offer.secret)}
                  </code>
                </dd>
              </div>
            </dl>
            <CopyButton text={secretForCopy(offer.secret)} what="Key" buttonText="Copy key" />
            <p className={enrol.note}>{MOVE_TEXT.keyPrivate}</p>
          </div>
          <CodeField id={NEW_ID} label={MOVE_TEXT.newCode} value={code} onChange={setCode} error={problems.code} />
          {failed}
          {leaveAsk}
          {buttons(busy ? MOVE_TEXT.moving : MOVE_TEXT.move)}
        </form>
      )}
      {step === "done" && (
        <div className={styles.dialogForm}>
          <p ref={focusOnMount} tabIndex={-1} role="status">
            {MOVE_TEXT.done}
          </p>
          <div className={styles.buttons}>
            <button type="button" className="btn btn-primary" onClick={onClose}>
              {MOVE_TEXT.doneButton}
            </button>
          </div>
        </div>
      )}
    </ActionDialog>
  );
}
