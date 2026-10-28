"use client";

// Keycloak sign-in (#435). While the server signs organisation users in through Keycloak
// (GET /auth/session says keycloak: true), the sign-in page shows only "Sign in with
// Keycloak": a plain link to the backend's start, which goes on to Keycloak's page for the
// password and the two-step code, and comes back signed in. A failed one comes back here
// with ?keycloak_error=<code>, shown in plain words (keycloakProblem), and that view shows
// at once. Otherwise the page asks the server first, so it never offers a form that can't
// work: while it waits, only the heading; if it gets no answer, a message and Try again.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { currentSession, keycloakOn } from "@/lib/api";
import { keycloakStartPath } from "./signIn";
import { SignedOutNotice } from "./SignedOutNotice";
import { SignInForm } from "./SignInForm";
import styles from "./login.module.css";

interface Props {
  note: string | null; // after signing out or a session ending (SignedOutNotice)
  next: string; // where to go once signed in (pathAfterSignIn)
  problem: string | null; // a failed Keycloak sign-in (keycloakProblem)
}

type Mode = "asking" | "password" | "keycloak" | "no_answer";

export const NO_ANSWER = "Could not reach Kaagaz to start signing in. Check your internet connection, then try again.";

export function SignInChoice({ note, next, problem }: Props) {
  const [mode, setMode] = useState<Mode>(problem ? "keycloak" : "asking");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (problem) return; // only a Keycloak sign-in comes back with one
    let live = true;
    currentSession().then(
      (reply) => live && setMode(keycloakOn(reply) ? "keycloak" : "password"),
      () => live && setMode("no_answer"),
    );
    return () => {
      live = false;
    };
  }, [problem, attempt]);

  if (mode === "keycloak") return <KeycloakSignIn note={note} next={next} problem={problem} />;
  if (mode === "password") return <SignInForm note={note} next={next} />;
  return (
    <div className={styles.form}>
      <div className={styles.intro}>
        <h1 className={styles.title}>Sign in to Kaagaz</h1>
      </div>
      {mode === "no_answer" && (
        <>
          <div className={styles.banner} role="alert">
            <Icon name="alert" />
            {NO_ANSWER}
          </div>
          <button
            type="button"
            className="btn btn-block"
            onClick={() => {
              setMode("asking");
              setAttempt((n) => n + 1);
            }}
          >
            Try again
          </button>
        </>
      )}
    </div>
  );
}

export function KeycloakSignIn({ note, next, problem }: Props) {
  return (
    <div className={styles.form}>
      <div className={styles.intro}>
        <h1 className={styles.title}>Sign in to Kaagaz</h1>
        <p className={styles.lead}>Your organisation signs in with Keycloak. It asks for your password and your two-step code.</p>
      </div>
      <SignedOutNotice message={note} />
      {problem && (
        <div className={styles.banner} role="alert">
          <Icon name="alert" />
          {problem}
        </div>
      )}
      <a href={keycloakStartPath(next)} className={`btn btn-primary btn-block ${styles.submit}`}>
        Sign in with Keycloak
      </a>
    </div>
  );
}
