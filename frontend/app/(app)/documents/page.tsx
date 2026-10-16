"use client";

// /documents: the documents screen. ?folder=…&sub=1&tag=…&type=…&from=…&to=…
// pick the folder and the filters (components/documents/libraryQuery.ts); ?q=… starts a
// search (the header's search box, G5). The URL is read inside Suspense, as Next.js requires.

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { DocumentsView } from "@/components/documents/DocumentsView";
import { libraryFromSearch } from "@/components/documents/libraryQuery";

function DocumentsRoute() {
  const search = useSearchParams().toString();
  // One view object per address, so the screen asks for its list only when the address changes.
  const library = useMemo(() => libraryFromSearch(new URLSearchParams(search)), [search]);
  return <DocumentsView library={library} startQ={new URLSearchParams(search).get("q") ?? ""} />;
}

export default function DocumentsPage() {
  return (
    <Suspense
      fallback={
        <div className="page">
          <p className="muted" role="status">
            Loading your documents…
          </p>
        </div>
      }
    >
      <DocumentsRoute />
    </Suspense>
  );
}
