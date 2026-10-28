"use client";

// Keycloak sign-in (#435). While the server signs organisation users in through Keycloak
// (GET /auth/session says keycloak: true), the sign-in page shows only "Sign in with
// Keycloak": a plain link to the backend's start, which goes on to Keycloak's page for the
// password and the two-step code, and comes back signed in. A failed one comes back here
// with ?keycloak_error=<code>, shown in plain words (keycloakProblem). Until the server
// answers, and whenever Keycloak is off, the page is the e-mail and password form as before.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { currentSession } from "@/lib/api";
import { keycloakOn, keycloakStartPath } from "./signIn";
import { SignedOutNotice } from "./SignedOutNotice";
import { SignInForm } from "./SignInForm";
import styles from "./login.module.css";

interface Props {
  note: string | null; // after signing out or a session ending (SignedOutNotice)
  next: string; // where to go once signed in (pathAfterSignIn)
  problem: string | null; // a failed Keycloak sign-in (keycloakProblem)
}

export function SignInChoice({ note, next, problem }: Props) {
  const [keycloak, setKeycloak] = useState(false);

  useEffect(() => {
    let live = true;
    currentSession()
      .then((reply) => {
        if (live) setKeycloak(keycloakOn(reply));
      })
      .catch(() => {}); // no answer: the form stays, and its sign-in says what is wrong
    return () => {
      live = false;
    };
  }, []);

  return keycloak ? (
    <KeycloakSignIn note={note} next={next} problem={problem} />
  ) : (
    <SignInForm note={note} next={next} />
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
