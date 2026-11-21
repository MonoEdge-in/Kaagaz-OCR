"use client";

// Account, WhatsApp (#603, PRD 7 section 15): connect a WhatsApp number, the bot messaging first.
// 1. Connect WhatsApp: a fresh company sign-in for WhatsApp first (Keycloak's own page), and the page
//    comes back with ?reauth=whatsapp (a failed check comes back with ?reauth_error and proves
//    nothing). Asking for the code needs that check; typing the code and confirming the phone way
//    need only the ten-minute WhatsApp proof it set, and when that is gone (403 proof_needed) the
//    page goes back to this step. Disconnect asks for no sign-in.
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
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  ApiError,
  askWhatsAppCode,
  confirmPhoneCode,
  disconnectWhatsApp,
  phoneCodeState,
  verifyWhatsAppCode,
  whatsappMe,
} from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import { beginKeycloakReauth, isKeycloakReauthRequired, keycloakStartFailure } from "@/lib/keycloak";
import type { WhatsAppCode, WhatsAppMe } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";
import {
  CONSENT_TEXT,
  NOT_AGREED,
  PREFIX,
  PRIVACY_TEXT,
  maskedNumber,
  normalizeNumber,
  numberProblem,
  stateText,
} from "./whatsapp";
import styles from "./WaysToSignIn.module.css";

type Step =
  | { kind: "idle" }
  | { kind: "prove" }
  | { kind: "number" }
  | { kind: "code"; number: string }
  | { kind: "phone"; number: string; shown: WhatsAppCode };


/** The server wants the company sign-in again: its fresh check, or the ten-minute WhatsApp one, is gone. */
export const needsProof = (caught: unknown) =>
  isKeycloakReauthRequired(caught) || (caught instanceof ApiError && caught.status === 403 && caught.code === "proof_needed");

/** The message of a refusal, or the plain words for a lost connection. */
function said(caught: unknown): string {
  return caught instanceof ApiError ? caught.message : errorWords(caught);
}

export function WhatsAppConnect({ params }: { params: Record<string, string | undefined> }) {
  // A check that came back with an error (?reauth_error) proved nothing.
  const returned = params.reauth === "whatsapp" && !params.reauth_error;
  const [stale, setStale] = useState(false); // the server said the proof ran out: sign in again
  const reauthAvailable = returned && !stale;
  const [me, setMe] = useState<WhatsAppMe | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [step, setStep] = useState<Step>(returned ? { kind: "number" } : { kind: "idle" });
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
      setMe(await disconnectWhatsApp()); // the server asks for no sign-in to disconnect
      setAsking(false);
      setDone("WhatsApp disconnected.");
    } catch (caught) {
      setError(said(caught));
    } finally {
      setBusy(false);
    }
  }

  const proofGone = () => {
    setStale(true);
    setStep({ kind: "prove" });
  };

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
      {done && (
        <p className={styles.done} role="status">
          {done}
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
      {step.kind === "prove" && (
        <Prove reauthAvailable={reauthAvailable} onProved={() => setStep({ kind: "number" })} />
      )}
      {step.kind === "number" && (
        <NumberStep
          onProof={proofGone}
          onSent={(number, reply) =>
            setStep(reply.sent ? { kind: "code", number } : { kind: "phone", number, shown: reply })
          }
        />
      )}
      {step.kind === "code" && (
        <CodeStep
          number={step.number}
          onConnected={connected}
          onProof={proofGone}
          onOtherWay={(shown) => setStep({ kind: "phone", number: step.number, shown })}
        />
      )}
      {step.kind === "phone" && <PhoneStep number={step.number} shown={step.shown} onConnected={connected} onProof={proofGone} />}
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

/** Step 1: the company sign-in, for this action only: it comes back here and the number is asked next. */
function Prove({ reauthAvailable, onProved }: { reauthAvailable: boolean; onProved(): void }) {
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setFailure(null);
    if (reauthAvailable) {
      onProved();
      return;
    }
    setBusy(true);
    try {
      await beginKeycloakReauth("whatsapp", window.location.pathname);
    } catch (failed) {
      setFailure(keycloakStartFailure(failed));
      setBusy(false);
    }
  }

  return (
    <form className="stack" method="post" noValidate onSubmit={submit}>
      <p>Verify with company sign-in to continue. Nothing changes until you return and repeat the action.</p>
      {failure && (
        <p className="error-text" role="alert">
          {failure}
        </p>
      )}
      <div className="actions">
        <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
          Verify with company sign-in
        </button>
      </div>
    </form>
  );
}

/** Step 2: the number, the privacy text and the tick. */
function NumberStep({ onSent, onProof }: { onSent(number: string, reply: WhatsAppCode): void; onProof(): void }) {
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
      if (needsProof(caught)) return onProof();
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
  onProof,
  onOtherWay,
}: {
  number: string;
  onConnected(me: WhatsAppMe): void;
  onProof(): void;
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
      if (needsProof(caught)) return onProof();
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
function PhoneStep({
  number,
  shown,
  onConnected,
  onProof,
}: {
  number: string;
  shown: WhatsAppCode;
  onConnected(me: WhatsAppMe): void;
  onProof(): void;
}) {
  const [state, setState] = useState<"waiting" | "confirmed" | "expired">("waiting");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The app's one poller (lib/usePoll.ts): every 3 then 5 seconds while it waits, paused while hidden.
  const poll = usePoll<"waiting" | "confirmed" | "expired">({
    load: async () => (await phoneCodeState()).state,
    onResult: setState,
    onFailure: () => undefined, // asked again next time
    again: (next) => next === "waiting",
    signature: (next) => next,
  });
  useEffect(() => poll.later(), [poll]);

  async function confirm() {
    setBusy(true);
    try {
      onConnected(await confirmPhoneCode());
    } catch (caught) {
      if (needsProof(caught)) return onProof();
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
