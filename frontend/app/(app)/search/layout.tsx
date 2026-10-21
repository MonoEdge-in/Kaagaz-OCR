import type { ReactNode } from "react";

// The tab's title: the section's name only, never a search's words (app/layout.tsx).
export const metadata = { title: "Search" };

export default function SearchLayout({ children }: { children: ReactNode }) {
  return children;
}
