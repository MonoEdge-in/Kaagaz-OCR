"use client";

// /documents/[id]: one document, its page viewer and the values read from it.
// ?page=n&region=<encoded> (from a source card or review item) opens on that
// spot, highlighted. The URL is read inside Suspense, as Next.js requires.

import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { DocumentView } from "./DocumentView";

function DocumentRoute() {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  return (
    <DocumentView
      key={params.id}
      documentId={params.id}
      pageParam={search.get("page")}
      regionParam={search.get("region")}
    />
  );
}

export default function DocumentPage() {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <DocumentRoute />
    </Suspense>
  );
}
