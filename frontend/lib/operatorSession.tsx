"use client";

// Who is signed in on the operator (super admin) screens. Separate from
// SessionProvider (lib/session.tsx), which is for customer users: an operator
// belongs to no organisation, and its cookie is sent only to /api/operator.
// The provider asks GET /api/operator/me once, shows a loading state until it
// knows, and leaves for /operator/login when there is no session, or when any
// later reply says it has ended (401 not_signed_in). Always a full page load,
// so nothing of the console stays in the tab's memory.

import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import styles from "@/components/shell/Shell.module.css";
import { ApiError, onSessionEnded, operatorMe } from "./api";
import { sessionErrorMessage } from "./session";
import { leavePage } from "./sessionChannel";
import type { OperatorMe } from "./types";

export const OPERATOR_SIGN_IN = "/operator/login";

const OperatorContext = createContext<OperatorMe | null>(null);

type Loaded =
  | { kind: "loading" }
  | { kind: "signed_in"; me: OperatorMe }
  | { kind: "signed_out" }
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
        const ended = error instanceof ApiError && error.status === 401;
        setState(ended ? { kind: "signed_out" } : { kind: "error", message: sessionErrorMessage(error) });
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  // The session ends while a screen is open (30 minutes, or signed out elsewhere).
  useEffect(() => {
    onSessionEnded(() => setState({ kind: "signed_out" }));
    return () => onSessionEnded(null);
  }, []);

  useEffect(() => {
    if (state.kind === "signed_in") hadSession.current = true;
    if (state.kind === "signed_out") leavePage(hadSession.current ? `${OPERATOR_SIGN_IN}?ended=1` : OPERATOR_SIGN_IN);
  }, [state.kind]);

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
