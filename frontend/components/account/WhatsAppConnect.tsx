"use client";

// Account, WhatsApp (#603, PRD 7 section 15): connect a WhatsApp number, the bot messaging first.
// 1. Connect WhatsApp: the Kaagaz password again (and the 6-digit code with two-step on), or, for one
//    who signs in with Google only, Confirm with Google (the page comes back with ?proved=google).
// 2. The number (+91 filled in), the privacy text and the "I agree" tick. Send code.
// 3. The Kaagaz bot sends a code on WhatsApp; it is typed here (10 minutes, 5 tries). If it could not
//    be sent, or "Didn't get it?", the second way: Open WhatsApp (a QR code on a computer) sends
//    "Link <code>" from the phone, and once it came this page asks "Connect +91 ••••1234?".
// Connected: the last 4 digits, since when, last used, and Disconnect (asked first). The words are
// whatsapp.ts's; a refusal shows on its field, or under the form.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { CodeField } from "@/app/login/CodeField";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import { QrCode } from "@/components/admin/QrCode";
import libraryStyles from "@/components/documents/Library.module.css";
import { proofField } from "@/components/shell/twoStepAccount";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { PasswordField } from "@/components/ui/PasswordField";
import {
  ApiError,
  askWhatsAppCode,
  confirmPhoneCode,
  disconnectWhatsApp,
  phoneCodeState,
  proveForWhatsApp,
  proveWithGoogle,
  verifyWhatsAppCode,
  whatsappMe,
} from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { WhatsAppCode, WhatsAppMe } from "@/lib/types";
import {
  CONSENT_TEXT,
  NOT_AGREED,
  PREFIX,
  PRIVACY_TEXT,
  maskedNumber,
  normalizeNumber,
  numberProblem,
  pageNotice,
  stateText,
} from "./whatsapp";
import styles from "./WaysToSignIn.module.css";

type Step =
  | { kind: "idle" }
  | { kind: "prove" }
  | { kind: "number" }
  | { kind: "code"; number: string }
  | { kind: "phone"; number: string; shown: WhatsAppCode };

const POLL_MS = 3000;

/** The message of a refusal, or the plain words for a lost connection. */
function said(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : errorWords(caught);
}

export function WhatsAppConnect({ params }: { params: Record<string, string | undefined> }) {
  const notice = pageNotice(params);
  const [me, setMe] = useState<WhatsAppMe | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [step, setStep] = useState<Step>(notice.proved ? { kind: "number" } : { kind: "idle" });
  const [done, setDone] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    whatsappMe().then(
      (reply) => live && setMe(reply),
      (caught: unknown) => live && setFailed(errorWords(caught)),
    );
    return () => {
      live = false;
    };
  }, []);

  function connected(reply: WhatsAppMe) {
    setMe(reply);
    setStep({ kind: "idle" });
    setDone("WhatsApp connected. The Kaagaz bot sent you a welcome message.");
  }

  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      setMe(await disconnectWhatsApp());
      setAsking(false);
      setDone("WhatsApp disconnected.");
    } catch (caught) {
      setError(said(caught));
    } finally {
      setBusy(false);
    }
  }

  if (failed) {
    return (
      <p className="hint" role="status">
        Could not load WhatsApp. {failed}
      </p>
    );
  }
  if (!me) return <p className="hint">Loading…</p>;
  return (
    <section className={styles.ways} aria-label="WhatsApp">
      {(done ?? notice.error) && (
        <p className={done ? styles.done : styles.error} role={done ? "status" : "alert"}>
          {done ?? notice.error}
        </p>
      )}
      {step.kind === "idle" && (
        <div className="stack">
          <p>{stateText(me)}</p>
          {me.state === "none" && (
            <button type="button" className="btn btn-primary" onClick={() => setStep({ kind: "prove" })}>
              Connect WhatsApp
            </button>
          )}
          {me.state === "connected" && (
            <button type="button" className="btn" onClick={() => setAsking(true)}>
              Disconnect
            </button>
          )}
        </div>
      )}
      {step.kind === "prove" && <Prove me={me} onProved={() => setStep({ kind: "number" })} />}
      {step.kind === "number" && (
        <NumberStep
          onSent={(number, reply) =>
            setStep(reply.sent ? { kind: "code", number } : { kind: "phone", number, shown: reply })
          }
        />
      )}
      {step.kind === "code" && (
        <CodeStep
          number={step.number}
          onConnected={connected}
          onOtherWay={(shown) => setStep({ kind: "phone", number: step.number, shown })}
        />
      )}
      {step.kind === "phone" && <PhoneStep number={step.number} shown={step.shown} onConnected={connected} />}
      <ConfirmDialog
        open={asking}
        title="Disconnect WhatsApp?"
        confirmLabel="Disconnect"
        busy={busy}
        error={error}
        onConfirm={() => void disconnect()}
        onCancel={() => setAsking(false)}
      >
        <p>This chat will no longer reach your Kaagaz files. Messages already on your phone stay there.</p>
      </ConfirmDialog>
    </section>
  );
}

/** Step 1: the password (and the code), or Google for one who signs in with Google only. */
function Prove({ me, onProved }: { me: WhatsAppMe; onProved(): void }) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [problems, setProblems] = useState<{ password: string | null; code: string | null }>({ password: null, code: null });
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const found = {
      password: !me.google && password === "" ? "Enter your password." : null,
      code: me.totp ? codeError(code) : null,
    };
    setProblems(found);
    setFailure(null);
    if (found.password || found.code) return;
    setBusy(true);
    const given = me.totp ? normalizeCode(code) : undefined;
    try {
      if (me.google) {
        window.location.href = (await proveWithGoogle(given)).url; // back here with ?proved=google
        return;
      }
      await proveForWhatsApp(password, given);
      onProved();
    } catch (caught) {
      const field = proofField(caught);
      setCode(""); // a code works once
      setBusy(false);
      if (field) setProblems({ password: field === "password" ? said(caught) : null, code: field === "code" ? said(caught) : null });
      else setFailure(said(caught));
    }
  }

  return (
    <form className="stack" method="post" noValidate onSubmit={submit}>
      <p>{me.google ? "Confirm it's you with Google to continue." : "Type your Kaagaz password to continue."}</p>
      {!me.google && (
        <PasswordField
          id="whatsapp-password"
          label="Kaagaz password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
          required
          error={problems.password}
        />
      )}
      {me.totp && (
        <CodeField
          id="whatsapp-two-step"
          label="6-digit code"
          value={code}
          onChange={setCode}
          error={problems.code}
          hint="From your authenticator app."
          enterKeyHint="done"
        />
      )}
      {failure && (
        <p className="error-text" role="alert">
          {failure}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
          {me.google ? "Confirm with Google" : "Continue"}
        </button>
      </div>
    </form>
  );
}

/** Step 2: the number, the privacy text and the tick. */
function NumberStep({ onSent }: { onSent(number: string, reply: WhatsAppCode): void }) {
  const [number, setNumber] = useState(PREFIX);
  const [agreed, setAgreed] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const found = numberProblem(number) ?? (agreed ? null : NOT_AGREED);
    setProblem(found);
    if (found) return;
    setBusy(true);
    try {
      onSent(normalizeNumber(number), await askWhatsAppCode(normalizeNumber(number), "sent"));
    } catch (caught) {
      setProblem(said(caught));
      setBusy(false);
    }
  }

  return (
    <form className="stack" method="post" noValidate onSubmit={submit}>
      <div className="field">
        <label htmlFor="whatsapp-number">Your WhatsApp number</label>
        <input
          id="whatsapp-number"
          className="input"
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          value={number}
          onChange={(event) => setNumber(event.target.value)}
        />
      </div>
      <p className="hint">{PRIVACY_TEXT}</p>
      <label className={libraryStyles.check}>
        <input type="checkbox" checked={agreed} onChange={(event) => setAgreed(event.target.checked)} />
        {CONSENT_TEXT}
      </label>
      {problem && (
        <p className="error-text" role="alert">
          {problem}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
          Send code on WhatsApp
        </button>
      </div>
    </form>
  );
}

/** Step 3: the code the bot sent; or the second way when it never came. */
function CodeStep({
  number,
  onConnected,
  onOtherWay,
}: {
  number: string;
  onConnected(me: WhatsAppMe): void;
  onOtherWay(shown: WhatsAppCode): void;
}) {
  const [code, setCode] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(send: () => Promise<void>) {
    setBusy(true);
    setProblem(null);
    try {
      await send();
    } catch (caught) {
      setProblem(said(caught));
      setBusy(false);
    }
  }

  return (
    <form
      className="stack"
      method="post"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        const found = codeError(code);
        if (found) setProblem(found);
        else if (!busy) void run(async () => onConnected(await verifyWhatsAppCode(normalizeCode(code))));
      }}
    >
      <p>The Kaagaz bot sent a code to {maskedNumber(number)} on WhatsApp. Type it here.</p>
      <CodeField id="whatsapp-code" label="Code from WhatsApp" value={code} onChange={setCode} error={problem} enterKeyHint="done" />
      <div className="actions">
        <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
          Connect
        </button>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={busy}
          onClick={() => void run(async () => onOtherWay(await askWhatsAppCode(number, "phone")))}
        >
          Didn&apos;t get it? Use WhatsApp instead
        </button>
      </div>
    </form>
  );
}

/** The second way: Open WhatsApp (or scan) sends "Link <code>"; once it came, "Connect +91 ••••1234?". */
function PhoneStep({ number, shown, onConnected }: { number: string; shown: WhatsAppCode; onConnected(me: WhatsAppMe): void }) {
  const [state, setState] = useState<"waiting" | "confirmed" | "expired">("waiting");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (state !== "waiting") return;
    const timer = setInterval(() => {
      phoneCodeState().then((reply) => setState(reply.state), () => undefined); // asked again next time
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [state]);

  async function confirm() {
    setBusy(true);
    try {
      onConnected(await confirmPhoneCode());
    } catch (caught) {
      setProblem(said(caught));
      setBusy(false);
    }
  }

  if (state === "expired") return <p role="alert">That code expired. Reload the page and start again.</p>;
  if (state === "confirmed") {
    return (
      <div className="stack">
        <p>Connect {maskedNumber(number)}?</p>
        {problem && (
          <p className="error-text" role="alert">
            {problem}
          </p>
        )}
        <div className="actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void confirm()}>
            Connect
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="stack">
      <p>
        We couldn&apos;t send a code to {maskedNumber(number)}. Open WhatsApp on that phone and send the message it
        fills in: Link {shown.code}. Then come back here.
      </p>
      {shown.link && (
        <>
          <a className="btn btn-primary" href={shown.link} target="_blank" rel="noopener noreferrer">
            Open WhatsApp
          </a>
          <QrCode value={shown.link} label="QR code that opens WhatsApp with the message" />
        </>
      )}
      <p className="hint" role="status">
        Waiting for your message…
      </p>
    </div>
  );
}
