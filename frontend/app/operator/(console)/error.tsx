"use client";

// An operator console screen that throws while it draws (#246 A8): this page, inside the
// console's top bar, instead of Next's bare "This page couldn't load". The signed-in session
// stays. Try again draws the screen again with fresh data; Details holds the error's words
// (a server error shows only its id: Next hides its message). Like app/(app)/error.tsx.

import Link from "next/link";
import { useEffect } from "react";
import { EmptyState } from "@/components/ui/EmptyState";

export default function ConsoleError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <main className="page">
      <EmptyState
        icon="alert"
        title="This console screen hit a problem"
        headingLevel={1}
        action={
          <>
            <button type="button" className="btn btn-primary" onClick={() => retry()}>
              Try again
            </button>
            <Link className="btn" href="/operator">
              Back to the console
            </Link>
          </>
        }
      >
        <p>Nothing was changed. Try again, and if it happens again, send the details below.</p>
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
