"use client";

// A throw in the root layout or the shell itself (the sidebar, the top bar, the upload tray),
// which app/(app)/error.tsx cannot catch: this page replaces the whole document, so it brings
// its own <html>, <body> and few styles (Next does not load the app's stylesheets here, so the
// handoff's colour values are written out, not read from tokens). Light or dark follows the
// system. Try again draws the app again; Details holds the error's words.

import Link from "next/link";
import { useEffect } from "react";

const STYLE = `
  :root { color-scheme: light dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px;
    font: 15px/1.5 system-ui, sans-serif; background: #f6f5f1; color: #111113; }
  main { max-width: 32rem; display: flex; flex-direction: column; gap: 16px; }
  h1 { margin: 0; font-size: 1.5rem; }
  p { margin: 0; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; }
  button, a { min-height: 40px; padding: 0 18px; border-radius: 999px; border: 1px solid #1e4396;
    font: inherit; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center;
    text-decoration: none; background: transparent; color: #1e4396; }
  button { background: #1e4396; color: #fff; }
  code { overflow-wrap: anywhere; }
  @media (prefers-color-scheme: dark) {
    body { background: #111113; color: #f2f1ec; }
    button, a { border-color: #8fa8f0; color: #8fa8f0; }
    button { background: #8fa8f0; color: #111113; }
  }
`;

export default function GlobalError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <html lang="en">
      <body>
        <style>{STYLE}</style>
        <title>Something went wrong · Kaagaz</title>
        <main>
          <h1>Kaagaz hit a problem</h1>
          <p>Your documents are safe. Try again, and if it happens again, send us the details below.</p>
          <div className="actions">
            <button type="button" onClick={() => retry()}>
              Try again
            </button>
            <Link href="/documents">Go to Documents</Link>
          </div>
          <details>
            <summary>Details</summary>
            <code>
              {error.message}
              {error.digest ? ` (${error.digest})` : ""}
            </code>
          </details>
        </main>
      </body>
    </html>
  );
}
