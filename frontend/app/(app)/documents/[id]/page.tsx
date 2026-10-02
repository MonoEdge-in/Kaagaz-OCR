"use client";

// /documents/[id]: one document, its page viewer and the values read from it.
// ?page=n&region=<encoded> (from a source card or review item) opens on that
// spot, highlighted; &scanned=1 opens that page's Scanned text too. The URL is
// read inside Suspense, as Next.js requires.

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
      scannedParam={search.get("scanned")}
    />
  );
}

export default function DocumentPage() {
  return (
    <Suspense
      fallback={
        <div className="page">
          <p className="muted" role="status">
            Loading the document…
          </p>
        </div>
      }
    >
      <DocumentRoute />
    </Suspense>
  );
}
