"use client";

// /search?q=…&in=names: the top bar's search (components/search/SearchScreen.tsx). The
// address is read inside Suspense, as Next.js requires.

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { searchFromAddress, SearchScreen } from "@/components/search/SearchScreen";

function SearchRoute() {
  const { q, mode } = searchFromAddress(useSearchParams());
  return <SearchScreen q={q} mode={mode} />;
}

export default function SearchPage() {
  return (
    <Suspense fallback={null}>
      <SearchRoute />
    </Suspense>
  );
}
