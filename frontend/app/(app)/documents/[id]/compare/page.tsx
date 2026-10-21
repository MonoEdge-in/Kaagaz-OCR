"use client";

// /documents/[id]/compare?with=<other id>: two versions side by side (File management V11).
// The URL is read inside Suspense, as Next.js requires.

import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { CompareView } from "@/components/versions/CompareView";
import { VERSION_TEXT } from "@/components/versions/words";

function CompareRoute() {
  const params = useParams<{ id: string }>();
  const withId = useSearchParams().get("with") ?? "";
  return <CompareView key={`${params.id}:${withId}`} documentId={params.id} withId={withId} />;
}

export default function ComparePage() {
  return (
    <Suspense
      fallback={
        <div className="page">
          <p className="muted" role="status">
            {VERSION_TEXT.loading}
          </p>
        </div>
      }
    >
      <CompareRoute />
    </Suspense>
  );
}
