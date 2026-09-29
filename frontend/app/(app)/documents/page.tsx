"use client";

// /documents: the documents screen. ?folder=… picks the folder
// (components/documents/libraryQuery.ts). The URL is read inside Suspense, as
// Next.js requires.

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { DocumentsView } from "@/components/documents/DocumentsView";
import { ALL_DOCUMENTS, libraryFromSearch } from "@/components/documents/libraryQuery";

function DocumentsRoute() {
  const search = useSearchParams().toString();
  // One view object per address, so the screen asks for its list only when the address changes.
  // Only the folder is read from it: this screen has no filters.
  const library = useMemo(
    () => ({ ...ALL_DOCUMENTS, folderId: libraryFromSearch(new URLSearchParams(search)).folderId }),
    [search],
  );
  return <DocumentsView library={library} />;
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
