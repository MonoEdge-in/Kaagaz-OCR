"use client";

// Who is signed in, for every screen inside the app. The provider asks the
// backend once (GET /api/auth/me), shows a loading state until it knows, and
// sends the browser to /login when there is no session. Screens read the result
// with useSession(); it is a hint for what to show, the server still decides
// what each person may do.

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import styles from "@/components/shell/Shell.module.css";
import { ApiError, me as fetchMe } from "./api";
import type { Me } from "./types";

export interface Session {
  me: Me;
  refresh(): Promise<void>;
}

const SessionContext = createContext<Session | null>(null);

type Loaded =
  | { kind: "loading" }
  | { kind: "signed_in"; me: Me }
  | { kind: "signed_out" }
  | { kind: "error"; message: string };

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<Loaded>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  const load = useCallback(async (): Promise<Loaded> => {
    try {
      return { kind: "signed_in", me: await fetchMe() };
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) return { kind: "signed_out" };
      const message =
        error instanceof ApiError ? error.message : "Could not reach the server.";
      return { kind: "error", message };
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

  useEffect(() => {
    if (state.kind === "signed_out") router.replace("/login");
  }, [state.kind, router]);

  const refresh = useCallback(async () => {
    setState(await load());
  }, [load]);

  const session = useMemo<Session | null>(
    () => (state.kind === "signed_in" ? { me: state.me, refresh } : null),
    [state, refresh],
  );

  if (session) {
    return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
  }

  if (state.kind === "error") {
    return (
      <main className={styles.centered}>
        <div className="card stack">
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
      <p className="muted" role="status" aria-live="polite">
        {state.kind === "signed_out" ? "Taking you to sign in…" : "Loading…"}
      </p>
    </main>
  );
}

/** The signed-in person. Only usable inside the app layout (under SessionProvider). */
export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside SessionProvider");
  return session;
}
