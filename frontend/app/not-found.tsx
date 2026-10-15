// Any address the app does not have: its own calm page, in either theme, with
// one way back (UI touch-up council, 15 Oct 2026).

import Link from "next/link";
import { EmptyState } from "@/components/ui/EmptyState";

export default function NotFound() {
  return (
    <main className="page">
      <EmptyState
        icon="search-off"
        title="This page isn't here"
        headingLevel={1}
        action={
          <Link className="btn btn-primary" href="/documents">
            Go to Documents
          </Link>
        }
      >
        <p>The link may be old or mistyped.</p>
      </EmptyState>
    </main>
  );
}
