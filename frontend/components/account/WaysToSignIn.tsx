"use client";

// Account, Ways to sign in (#457 phase 1): the person's own ways in. The first row is always
// their email and password; then one row per provider the server switched on: Not connected
// [Connect], connected with its email and day [Disconnect], or Waiting for your admin's
// approval [Cancel]. Connect asks for the Kaagaz password first (and the 6-digit code from
// anyone with two-step sign-in on), then the browser goes to the provider's account chooser
// and comes back here with ?connected=, ?waiting= or ?error=; that notice shows once the list
// has loaded. Disconnect asks first, with the password; cancelling a request that still waits
// needs no password. Every account keeps its Kaagaz password, so nobody is locked out. A
// missing or wrong password or code shows on its field, which takes focus. The words are
// signInWays.ts's. #507: under the list, the organisation's rules for this person (Google only,
// the company domains); a session that may only connect Google is sent here (lib/session.tsx).

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { flushSync } from "react-dom";
import { CodeField } from "@/app/login/CodeField";
import { codeError, normalizeCode } from "@/app/login/twoStep";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { proofField } from "@/components/shell/twoStepAccount";
import { Icon } from "@/components/ui/Icon";
import { PasswordField } from "@/components/ui/PasswordField";
import { ApiError, connectProvider, identities, removeIdentity } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { ConnectedIdentity, WaysToSignIn as Ways } from "@/lib/types";
import {
  disconnectQuestion,
  introText,
  needsCode,
  pageNotice,
  passwordRow,
  providerName,
  rowText,
  rulesText,
  waysLoadText,
} from "./signInWays";
import styles from "./WaysToSignIn.module.css";

type Asking = { kind: "connect"; provider: string } | { kind: "remove"; provider: string; identity: ConnectedIdentity };

const OFF = "Signing in with Google isn't switched on for Kaagaz yet.";
const PASSWORD_ID = "ways-password";
const CODE_ID = "ways-code";

/** The line under the page's title, naming only the providers that are on (#457 review). */
export function WaysIntro() {
  const { me } = useSession();
  const text = introText(me.providers);
  return text ? <p className="hint">{text}</p> : null;
}

export function WaysToSignIn({ params }: { params: Record<string, string | undefined> }) {
  const { me } = useSession();
  const [ways, setWays] = useState<Ways | null>(null);
  // Issue 468: why the list did not load, with Try again (not when Google is switched off).
  const [failure, setFailure] = useState<{ text: string; retry: boolean } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [asking, setAsking] = useState<Asking | null>(null);
  const [said, setSaid] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    identities().then(
      (reply) => {
        if (live) setWays(reply);
      },
      (caught: unknown) => {
        if (!live) return;
        const off = caught instanceof ApiError && caught.status === 404;
        setFailure(off ? { text: OFF, retry: false } : { text: waysLoadText(caught), retry: true });
      },
    );
    return () => {
      live = false;
    };
  }, [attempt]);

  const rules = ways ? rulesText(ways.rules, me.google_link_required === true) : null;
  const connected = params.connected ? ways?.identities.find((row) => row.provider === params.connected) : undefined;
  const notice = said
    ? { kind: "done" as const, text: said }
    : ways !== null || failure !== null
      ? pageNotice(params, connected?.email)
      : null;

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
      {rules && <p className="hint">{rules}</p>}
      {failure && (
        <div className={styles.loadFailed}>
          <p className="hint" role="status">
            {failure.text}
          </p>
          {failure.retry && (
            <button
              type="button"
              className="btn"
              onClick={() => {
                setFailure(null);
                setAttempt((n) => n + 1);
              }}
            >
              <Icon name="refresh" />
              Try again
            </button>
          )}
        </div>
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

type Problems = { password: string | null; code: string | null };

/** Connect's "Type your Kaagaz password to continue" (and the code), or the question before
 * Disconnect with the password, or before cancelling a waiting request, with none. */
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
  const [problems, setProblems] = useState<Problems>({ password: null, code: null });
  const [masked, setMasked] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = providerName(asking.provider);
  const question = asking.kind === "remove" ? disconnectQuestion(asking.provider, asking.identity) : null;
  // A request that still waits for approval is cancelled without the password (#457 review).
  const withPassword = !(asking.kind === "remove" && asking.identity.status === "pending");

  /** Commit the messages first, so the field is described when it gets focus. */
  function showOnFields(found: Problems) {
    flushSync(() => setProblems(found));
    document.getElementById(found.password ? PASSWORD_ID : CODE_ID)?.focus();
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setMasked(true);
    setFailure(null);
    const found = {
      password: withPassword && password === "" ? "Enter your password." : null,
      code: withCode ? codeError(code) : null,
    };
    if (found.password || found.code) {
      showOnFields(found);
      return;
    }
    setProblems(found);
    setBusy(true);
    try {
      if (asking.kind === "connect") {
        const { url } = await connectProvider(asking.provider, password, withCode ? normalizeCode(code) : undefined);
        window.location.href = url; // the provider's account chooser; it comes back to this page
        return;
      }
      const reply = await removeIdentity(asking.identity.id, withPassword ? password : undefined);
      onRemoved(reply, asking.identity.status === "pending" ? "Request cancelled." : `${name} disconnected.`);
    } catch (caught) {
      const field = proofField(caught);
      const message = caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.";
      setCode(""); // a code works once
      setBusy(false);
      if (field) showOnFields({ password: field === "password" ? message : null, code: field === "code" ? message : null });
      else setFailure(message);
    }
  }

  return (
    <ActionDialog title={question?.title ?? `Connect ${name}`} onClose={onClose} canClose={() => !busy}>
      <form method="post" noValidate onSubmit={submit}>
        <p>{question?.text ?? "Type your Kaagaz password to continue."}</p>
        {withPassword && (
          <PasswordField
            id={PASSWORD_ID}
            label="Kaagaz password"
            value={password}
            onChange={(value) => {
              setPassword(value);
              if (problems.password) setProblems((was) => ({ ...was, password: null }));
            }}
            autoComplete="current-password"
            required
            error={problems.password}
            masked={masked}
            onMaskedChange={setMasked}
          />
        )}
        {withCode && (
          <CodeField
            id={CODE_ID}
            label="6-digit code"
            value={code}
            onChange={(value) => {
              setCode(value);
              if (problems.code) setProblems((was) => ({ ...was, code: codeError(value) }));
            }}
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
