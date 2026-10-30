"use client";

// Confirming an email from its link (#457 phase 0): the token is read from the fragment
// once, cleared from the address bar (takeToken) and sent; the page then says what
// happened; a sign-up waiting for approval is told so (the reply's ``pending``).

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ui/Icon";
import { confirmEmail } from "@/lib/api";
import { LINK_MISSING, confirmedText, linkError, takeToken } from "@/lib/emailLinks";
import login from "../../login/login.module.css";

type State = { kind: "working" } | { kind: "done"; text: string } | { kind: "failed"; message: string };

export function ConfirmEmail() {
  const started = useRef(false);
  const [state, setState] = useState<State>({ kind: "working" });

  useEffect(() => {
    if (started.current) return; // once: a link works once
    started.current = true;
    const token = takeToken();
    const sent = token === null ? Promise.reject(new Error(LINK_MISSING)) : confirmEmail(token);
    sent.then(
      (reply) => setState({ kind: "done", text: confirmedText(reply.pending) }),
      (caught: unknown) =>
        setState({ kind: "failed", message: token === null ? LINK_MISSING : linkError(caught, "confirm") }),
    );
  }, []);

  return (
    <div className={login.form}>
      <div className={login.intro}>
        <h1 className={login.title}>Confirm your email</h1>
        {state.kind === "working" && (
          <p className={login.lead} role="status">
            <span className="spinner" aria-hidden="true" /> Confirming…
          </p>
        )}
        {state.kind === "done" && (
          <p className={login.lead} role="status">
            {state.text}
          </p>
        )}
      </div>
      {state.kind === "failed" && (
        <div className={login.banner} role="alert">
          <Icon name="alert" />
          {state.message}
        </div>
      )}
      <Link href="/login" className={`btn btn-primary btn-block ${login.submit}`}>
        Go to sign in
      </Link>
    </div>
  );
}
