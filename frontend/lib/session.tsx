"use client";

// Who is signed in, for every screen inside the app. The provider asks the
// backend once (GET /api/auth/session: /auth/me's answer, or signed_in false with no
// session, a 200, so a signed-out visit logs no expected 401; issue 378), shows a
// loading state until it knows, and
// sends the browser to sign in when there is no session, or when any later
// reply says it has ended: to /login, coming back to /admin from the admin
// screens (signInPath, components/shell/gate.ts). Screens read the result
// with useSession(); it is a hint for what to show, the server still decides
// what each person may do.
//
// While the account has a temporary password (PRD A5), the provider shows the
// set-new-password screen instead of the app, whatever the page; the server
// refuses everything else until then. refresh() asks the server again, so the
// app opens once the new password is saved. An admin without two-step sign-in
// (MFA required, 16 Oct 2026; a session from before, or just after a temporary
// password) is signed out and sent to the sign-in, which sets it up.

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { appGate, endedPath, pageAfterSessionChange, signInPath, twoStepPath } from "@/components/shell/gate";
import { SetPasswordScreen } from "@/components/shell/SetPasswordScreen";
import styles from "@/components/shell/Shell.module.css";
import { ApiError, currentSession, logout, onSessionEnded } from "./api";
import { announceSession, leavePage, watchSession } from "./sessionChannel";
import type { Me } from "./types";

export interface Session {
  me: Me;
  /** Load who is signed in again (after a password change, say); the screen stays meanwhile. */
  refresh(): void;
}

const SessionContext = createContext<Session | null>(null);

type Loaded =
  | { kind: "loading" }
  | { kind: "signed_in"; me: Me }
  | { kind: "signed_out" }
  | { kind: "error"; message: string };

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [state, setState] = useState<Loaded>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  const load = useCallback(async (): Promise<Loaded> => {
    try {
      const found = await currentSession();
      return found.signed_in ? { kind: "signed_in", me: found.me } : { kind: "signed_out" };
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return { kind: "signed_out" };
      return { kind: "error", message: sessionErrorMessage(error) };
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    load().then((next) => {
      if (!cancelled) setState(next);
    });
    return () => {
      cancelled = true;
    };
  }, [load, attempt]);

  // A session that ends while a screen is open (an admin session runs out, say).
  useEffect(() => {
    onSessionEnded(() => setState({ kind: "signed_out" }));
    return () => onSessionEnded(null);
  }, []);

  // Once a session existed, the sign-in page is a full page load, so nothing
  // of that person (cached PDFs, earlier screens) stays in this tab's memory.
  // A first visit with no session has nothing to clear: a quick move is fine.
  const hadSession = useRef(false);
  useEffect(() => {
    if (state.kind === "signed_in") hadSession.current = true;
    if (state.kind !== "signed_out") return;
    if (hadSession.current) leavePage(endedPath(pathname));
    else router.replace(signInPath(pathname));
  }, [state.kind, router, pathname]);

  // Another tab signed out, or someone else signed in (lib/sessionChannel.ts).
  // Heard from the start: a tab that does not know yet who is signed in
  // (loading, or the last try failed) asks again, as the answer on its way
  // may be from before the change.
  useEffect(
    () =>
      watchSession((now) => {
        if (state.kind === "signed_in") {
          const to = pageAfterSessionChange(state.me.user.id, now, window.location);
          if (to !== null) leavePage(to);
        } else if (state.kind !== "signed_out") {
          setAttempt((n) => n + 1);
        }
      }),
    [state],
  );

  // The back button can bring back a page kept whole in memory (the
  // back-forward cache), even after signing out: it is hidden as it is put
  // away, so it never shows, and loads fresh when it comes back.
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

  const refresh = useCallback(() => setAttempt((n) => n + 1), []);

  const session = useMemo<Session | null>(
    () => (state.kind === "signed_in" ? { me: state.me, refresh } : null),
    [state, refresh],
  );

  const gate = session ? appGate(session.me) : null;
  useEffect(() => {
    if (gate !== "set_two_step") return;
    const go = () => {
      announceSession(null);
      leavePage(twoStepPath(pathname));
    };
    void logout().then(go, go); // signed out or not, the sign-in sets two-step up
  }, [gate, pathname]);

  if (session && gate !== "set_two_step") {
    return (
      <SessionContext.Provider value={session}>
        {appGate(session.me) === "set_password" ? (
          <SetPasswordScreen me={session.me} onChanged={session.refresh} />
        ) : (
          children
        )}
      </SessionContext.Provider>
    );
  }

  if (state.kind === "error") {
    return (
      <main className={styles.centered}>
        <div className={`card stack ${styles.centeredBox}`}>
          <p className="error-text" role="alert">
            {state.message}
          </p>
          <div>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setState({ kind: "loading" });
                setAttempt((n) => n + 1);
              }}
            >
              Try again
            </button>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className={styles.centered}>
      <p className={`delayed ${styles.loading}`} role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        {gate === "set_two_step"
          ? "Taking you to set up two-step sign-in…"
          : state.kind === "signed_out"
            ? "Taking you to sign in…"
            : "Loading…"}
      </p>
    </main>
  );
}

/** What to say when the app cannot ask the server who is signed in. */
export function sessionErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 429) {
    return "Kaagaz is busy right now. Wait a minute, then try again.";
  }
  if (error instanceof ApiError && error.status !== 0) {
    return "Kaagaz is not answering right now. Try again in a minute.";
  }
  return "Could not reach Kaagaz. Check your internet connection and try again.";
}

/** The signed-in person. Only usable inside the app layout (under SessionProvider). */
export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
