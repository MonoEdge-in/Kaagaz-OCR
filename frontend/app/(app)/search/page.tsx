"use client";

// /search?q=…&in=names: the top bar's search (components/search/SearchScreen.tsx). The
// address is read inside Suspense, as Next.js requires. The documents it finds lead
// back to this search (lib/backLink.ts, #436).

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { searchFromAddress, searchHref, SearchScreen } from "@/components/search/SearchScreen";
import { BackFrom } from "@/lib/backLink";

function SearchRoute() {
  const { q, mode } = searchFromAddress(useSearchParams());
  return (
    <BackFrom value={searchHref(q, mode)}>
      <SearchScreen q={q} mode={mode} />
    </BackFrom>
  );
}

export default function SearchPage() {
  return (
    <Suspense fallback={null}>
      <SearchRoute />
    </Suspense>
  );
}
