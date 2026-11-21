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
// Keycloak is the only sign-in (21 Nov 2026): no temporary password, no two-step set-up and no
// Google link gate remain here.

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  endedPath,
  pageAfterSessionChange,
  SEARCH_DRAFT,
  searchToKeep,
  signInPath,
} from "@/components/shell/gate";
import { KeycloakReauthNotice } from "@/components/shell/KeycloakReauthNotice";
import styles from "@/components/shell/Shell.module.css";
import { ApiError, currentSession, onSessionEnded } from "./api";
import { clearDrafts, flushDrafts, keepDraftsOf, saveDraft } from "./drafts";
import { leavePage, watchSession } from "./sessionChannel";
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
  // Issue 468: the sign-in comes back to this screen's path (endedPath's ?next=), and unsent work
  // waiting to be kept is written first (lib/drafts.ts), for the same person to get back. A
  // search's words go in that person's draft, not the address (SearchScreen restores them).
  const signedInId = state.kind === "signed_in" ? state.me.user.id : null;
  const hadSession = useRef(false);
  const lastUserId = useRef<string | null>(null);
  useEffect(() => {
    if (signedInId !== null) {
      hadSession.current = true;
      lastUserId.current = signedInId;
    }
    if (state.kind !== "signed_out") return;
    if (hadSession.current) {
      const kept = searchToKeep(window.location.pathname, window.location.search);
      if (kept !== null && lastUserId.current !== null) saveDraft(lastUserId.current, SEARCH_DRAFT, kept);
      flushDrafts();
      leavePage(endedPath(window.location.pathname));
    } else router.replace(signInPath(pathname));
  }, [state.kind, signedInId, router, pathname]);

  // Drafts kept in this tab by anyone else are dropped as soon as who is signed in is known.
  useEffect(() => {
    if (signedInId !== null) keepDraftsOf(signedInId);
  }, [signedInId]);

  // Another tab signed out, or someone else signed in (lib/sessionChannel.ts).
  // Heard from the start: a tab that does not know yet who is signed in
  // (loading, or the last try failed) asks again, as the answer on its way
  // may be from before the change.
  useEffect(
    () =>
      watchSession((now) => {
        if (state.kind === "signed_in") {
          const to = pageAfterSessionChange(state.me.user.id, now, window.location);
          if (to === null) return;
          clearDrafts(); // signed out, or someone else signed in: nothing of this person is kept
          leavePage(to);
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

  if (session) {
    return (
      <SessionContext.Provider value={session}>
        <KeycloakReauthNotice />
        {children}
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
        {state.kind === "signed_out" ? "Taking you to sign in…" : "Loading…"}
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

/** Read the session when a reusable dialog is rendered without the app provider. */
export function useOptionalSession(): Session | null {
  return useContext(SessionContext);
}
