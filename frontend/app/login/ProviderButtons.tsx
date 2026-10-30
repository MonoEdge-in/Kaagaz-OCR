"use client";

// Ways to sign in (#457 phase 1), above the sign-in form: a full-width "Continue with Google"
// for each provider the server switched on (GET /auth/session's `providers` while signed
// out), then the divider "or use your email". Each is a plain link to the API's start (a GET
// that goes on to the provider), so no provider script ever loads; the logo is inline. A
// failed provider sign-in comes back as ?oidc_error=<code> and is said here in plain words;
// "isn't connected" asks the server for its email (it never comes in the address). Never on
// the operator's sign-in page.

import { useEffect, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { currentSession, oidcNotice } from "@/lib/api";
import { oidcErrorMessage, providerButtons } from "./providerMessages";
import styles from "./login.module.css";

export function ProviderButtons({ next, error }: { next: string; error: string | null }) {
  const [providers, setProviders] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(
    error && error !== "not_connected" ? oidcErrorMessage(error) : null,
  );

  useEffect(() => {
    let live = true;
    currentSession()
      .then((reply) => {
        if (live && !reply.signed_in) setProviders(reply.providers ?? []);
      })
      .catch(() => {}); // no answer: the email and password form alone
    if (error === "not_connected") {
      oidcNotice()
        .then((reply) => {
          if (live) setMessage(oidcErrorMessage(error, reply.email ?? undefined));
        })
        .catch(() => {
          if (live) setMessage(oidcErrorMessage(error));
        });
    }
    return () => {
      live = false;
    };
  }, [error]);

  const buttons = providerButtons(providers, next);
  return (
    <>
      {message && (
        <div className={styles.banner} role="alert">
          <Icon name="alert" />
          {message}
        </div>
      )}
      {buttons.length > 0 && (
        <>
          {buttons.map((button) => (
            <a key={button.provider} className={`btn btn-block ${styles.provider}`} href={button.href}>
              <GoogleLogo />
              {button.label}
            </a>
          ))}
          <p className={styles.divider}>
            <span>or use your email</span>
          </p>
        </>
      )}
    </>
  );
}

/** Google's "G", drawn here: its four brand colours are Google's own, never tokens. */
function GoogleLogo() {
  return (
    <svg className={styles.providerLogo} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  );
}
