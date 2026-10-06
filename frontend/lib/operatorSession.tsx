"use client";

// Who is signed in on the operator (super admin) screens. Separate from
// SessionProvider (lib/session.tsx), which is for customer users: an operator
// belongs to no organisation, and its cookie is sent only to /api/operator.
// The provider asks GET /api/operator/me once, shows a loading state until it
// knows, and leaves for /operator/login when there is no session, or when any
// later reply says it has ended (401 not_signed_in). It also leaves by itself
// when the session's 30 minutes are up (asking the server first, as clocks
// differ), and when another tab signs out (the BroadcastChannel
// OPERATOR_CHANNEL, not the customer app's). Always a full page load, so
// nothing of the console stays in the tab's memory.

import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import styles from "@/components/shell/Shell.module.css";
import { OPERATOR_CHANNEL, RECHECK_MS, SIGNED_OUT, exitUrl, expiryDelay, meFailed } from "@/components/operator/logic";
import type { ExitReason } from "@/components/operator/logic";
import { onSessionEnded, operatorMe } from "./api";
import { sessionErrorMessage } from "./session";
import { leavePage } from "./sessionChannel";
import type { OperatorMe } from "./types";

let channel: BroadcastChannel | null = null;

/** One channel per tab, so a tab never hears its own sign-out. */
function tabChannel(): BroadcastChannel | null {
  if (typeof BroadcastChannel === "undefined") return null;
  channel ??= new BroadcastChannel(OPERATOR_CHANNEL);
  return channel;
}

/** Tell the other operator tabs that every session has ended. */
export function announceSignOut(): void {
  tabChannel()?.postMessage(SIGNED_OUT);
}

const OperatorContext = createContext<OperatorMe | null>(null);

type Loaded =
  | { kind: "loading" }
  | { kind: "signed_in"; me: OperatorMe }
  | { kind: "signed_out"; reason: ExitReason }
  | { kind: "error"; message: string };

export function OperatorSessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<Loaded>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const hadSession = useRef(false);

  useEffect(() => {
    let cancelled = false;
    operatorMe().then(
      (me) => !cancelled && setState({ kind: "signed_in", me }),
      (error: unknown) => {
        if (cancelled) return;
        const ended = meFailed(error) === "signed_out";
        setState(ended ? { kind: "signed_out", reason: "ended" } : { kind: "error", message: sessionErrorMessage(error) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // The session ends while a screen is open (30 minutes, or signed out elsewhere).
  useEffect(() => {
    onSessionEnded(() => setState({ kind: "signed_out", reason: "ended" }));
    const tab = tabChannel();
    const listener = (event: MessageEvent<unknown>) => {
      if (event.data === SIGNED_OUT) setState({ kind: "signed_out", reason: "signed_out" });
    };
    tab?.addEventListener("message", listener);
    return () => {
      onSessionEnded(null);
      tab?.removeEventListener("message", listener);
    };
  }, []);

  // The session's end: ask the server, whose 401 leaves (onSessionEnded); a
  // yes means this computer's clock is ahead, so ask again a little later.
  const expiresAt = state.kind === "signed_in" ? state.me.session.expires_at : null;
  useEffect(() => {
    if (expiresAt === null) return;
    let timer = 0;
    let stopped = false;
    const check = (delay: number) => {
      timer = window.setTimeout(() => operatorMe().then(() => !stopped && check(RECHECK_MS), () => undefined), delay);
    };
    check(expiryDelay(expiresAt, Date.now()));
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [expiresAt]);

  const leaveFor = state.kind === "signed_out" ? state.reason : null;
  useEffect(() => {
    if (state.kind === "signed_in") hadSession.current = true;
    if (leaveFor) leavePage(exitUrl(leaveFor, hadSession.current));
  }, [state.kind, leaveFor]);

  // The back button can bring back a page kept whole in memory after signing
  // out: hidden as it is put away, loaded fresh when it comes back.
  useEffect(() => {
    const onHide = (event: PageTransitionEvent) => {
      if (event.persisted) document.documentElement.style.visibility = "hidden";
    };
    const onShow = (event: PageTransitionEvent) => {
      if (event.persisted) window.location.reload();
    };
    window.addEventListener("pagehide", onHide);
    window.addEventListener("pageshow", onShow);
    return () => {
      window.removeEventListener("pagehide", onHide);
      window.removeEventListener("pageshow", onShow);
    };
  }, []);

  if (state.kind === "signed_in") {
    return <OperatorContext.Provider value={state.me}>{children}</OperatorContext.Provider>;
  }

  return (
    <main className={styles.centered}>
      {state.kind === "error" ? (
        <div className={`card stack ${styles.centeredBox}`}>
          <p className="error-text" role="alert">
            {state.message}
          </p>
          <div>
            <button type="button" className="btn" onClick={() => { setState({ kind: "loading" }); setAttempt((n) => n + 1); }}>
              Try again
            </button>
          </div>
        </div>
      ) : (
        <p className={`delayed ${styles.loading}`} role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" />
          {state.kind === "signed_out" ? "Taking you to sign in…" : "Loading…"}
        </p>
      )}
    </main>
  );
}

/** The signed-in operator. Only usable under OperatorSessionProvider. */
export function useOperator(): OperatorMe {
  const me = useContext(OperatorContext);
  if (!me) throw new Error("useOperator must be used inside OperatorSessionProvider");
  return me;
}
