"use client";

// Account, Ways to sign in (#457 phase 1): the person's own ways in. The first row is always
// their email and password; then one row per provider the server switched on: Not connected
// [Connect], connected with its email and day [Disconnect], or Waiting for your admin's
// approval [Cancel]. Connect asks for the Kaagaz password first (and the 6-digit code from
// anyone with two-step sign-in on), then the browser goes to the provider's account chooser
// and comes back here with ?connected=, ?waiting= or ?error=. Disconnect and Cancel ask first,
// with the password; every account keeps its Kaagaz password, so nobody is locked out. The
// words are signInWays.ts's.

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { CodeField } from "@/app/login/CodeField";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Icon } from "@/components/ui/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { ApiError, connectProvider, identities, removeIdentity } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ConnectedIdentity, WaysToSignIn as Ways } from "@/lib/types";
import { disconnectQuestion, needsCode, pageNotice, passwordRow, providerName, rowText } from "./signInWays";
import styles from "./WaysToSignIn.module.css";

type Asking = { kind: "connect"; provider: string } | { kind: "remove"; provider: string; identity: ConnectedIdentity };

const OFF = "Signing in with Google isn't switched on for Kaagaz yet.";
const NO_ANSWER = "Could not load your ways to sign in. Check your internet connection, then try again.";

export function WaysToSignIn({ params }: { params: Record<string, string | undefined> }) {
  const { me } = useSession();
  const [ways, setWays] = useState<Ways | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [asking, setAsking] = useState<Asking | null>(null);
  const [said, setSaid] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    identities().then(
      (reply) => {
        if (live) setWays(reply);
      },
      (caught: unknown) => {
        if (live) setFailure(caught instanceof ApiError && caught.status === 404 ? OFF : NO_ANSWER);
      },
    );
    return () => {
      live = false;
    };
  }, []);

  const connected = params.connected ? ways?.identities.find((row) => row.provider === params.connected) : undefined;
  const notice = said ? { kind: "done" as const, text: said } : pageNotice(params, connected?.email);

  return (
    <section className={styles.ways} aria-label="Ways to sign in">
      {notice && (
        <p className={notice.kind === "error" ? styles.error : styles.done} role={notice.kind === "error" ? "alert" : "status"}>
          <Icon name={notice.kind === "error" ? "alert" : "check-seal"} />
          {notice.text}
        </p>
      )}
      <ul className={styles.rows}>
        <li className={styles.row}>
          <span className={styles.rowText}>{passwordRow(me.user.email)}</span>
        </li>
        {ways?.providers.map((provider) => {
          const mine = ways.identities.find((row) => row.provider === provider) ?? null;
          return (
            <li key={provider} className={styles.row}>
              <span className={styles.rowText}>{rowText(provider, mine)}</span>
              {mine === null ? (
                <button type="button" className="btn" onClick={() => setAsking({ kind: "connect", provider })}>
                  Connect
                </button>
              ) : (
                <button
                  type="button"
                  className="btn"
                  onClick={() => setAsking({ kind: "remove", provider, identity: mine })}
                >
                  {mine.status === "pending" ? "Cancel" : "Disconnect"}
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {failure && (
        <p className="hint" role="status">
          {failure}
        </p>
      )}
      {asking && (
        <PasswordDialog
          asking={asking}
          withCode={asking.kind === "connect" && needsCode(me)}
          onClose={() => setAsking(null)}
          onRemoved={(reply, text) => {
            setWays(reply);
            setSaid(text);
            setAsking(null);
          }}
        />
      )}
    </section>
  );
}

/** Connect's "Type your Kaagaz password to continue" (and the code), or the question before
 * Disconnect or Cancel with the password. */
function PasswordDialog({
  asking,
  withCode,
  onClose,
  onRemoved,
}: {
  asking: Asking;
  withCode: boolean;
  onClose(): void;
  onRemoved(reply: Ways, text: string): void;
}) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codeProblem, setCodeProblem] = useState<string | null>(null);
  const [masked, setMasked] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = providerName(asking.provider);
  const question = asking.kind === "remove" ? disconnectQuestion(asking.provider, asking.identity) : null;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setFailure(null);
    if (password === "") {
      setFailure("Enter your password.");
      return;
    }
    const problem = withCode ? codeError(code) : null;
    setCodeProblem(problem);
    if (problem) return;
    setBusy(true);
    try {
      if (asking.kind === "connect") {
        const { url } = await connectProvider(asking.provider, password, withCode ? normalizeCode(code) : undefined);
        window.location.href = url; // the provider's account chooser; it comes back to this page
        return;
      }
      const reply = await removeIdentity(asking.identity.id, password);
      onRemoved(reply, asking.identity.status === "pending" ? "Request cancelled." : `${name} disconnected.`);
    } catch (caught) {
      setFailure(caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.");
      setCode(""); // a code works once
      setBusy(false);
    }
  }

  return (
    <ActionDialog title={question?.title ?? `Connect ${name}`} onClose={onClose} canClose={() => !busy}>
      <form method="post" noValidate onSubmit={submit}>
        <p>{question?.text ?? "Type your Kaagaz password to continue."}</p>
        <PasswordField
          id="ways-password"
          label="Kaagaz password"
          value={password}
          onChange={setPassword}
          autoComplete="current-password"
          required
          error={null}
          masked={masked}
          onMaskedChange={setMasked}
        />
        {withCode && (
          <CodeField
            id="ways-code"
            label="6-digit code"
            value={code}
            onChange={(value) => {
              setCode(value);
              if (codeProblem) setCodeProblem(codeError(value));
            }}
            error={codeProblem}
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
          <button type="button" className="btn" disabled={busy} onClick={onClose}>
            {question ? "Keep it" : "Cancel"}
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy} aria-busy={busy || undefined}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {question?.button ?? "Continue"}
          </button>
        </div>
      </form>
    </ActionDialog>
  );
}
