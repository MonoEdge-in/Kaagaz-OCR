"use client";

// A signed-in screen that throws while it draws: this page, inside the shell (the sidebar and
// the top bar stay), instead of Next's bare "This page couldn't load". Try again draws the
// screen again with fresh data. The error's own words sit under Details, so a person can
// send them to us; a server error shows only its id (Next hides its message).

import Link from "next/link";
import { useEffect } from "react";
import { EmptyState } from "@/components/ui/EmptyState";

export default function ScreenError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <main className="page">
      <EmptyState
        icon="alert"
        title="This screen hit a problem"
        headingLevel={1}
        action={
          <>
            <button type="button" className="btn btn-primary" onClick={() => retry()}>
              Try again
            </button>
            <Link className="btn" href="/documents">
              Go to Documents
            </Link>
          </>
        }
      >
        <p>Your documents are safe. Try again, and if it happens again, send us the details below.</p>
        <details>
          <summary>Details</summary>
          <p className="mono">
            {error.message}
            {error.digest ? ` (${error.digest})` : ""}
          </p>
        </details>
      </EmptyState>
    </main>
  );
}
