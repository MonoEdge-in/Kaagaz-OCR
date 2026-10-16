import type { ReactNode } from "react";

// The tab's title: the section's name only, never a document's (app/layout.tsx).
export const metadata = { title: "My words" };

export default function MyWordsLayout({ children }: { children: ReactNode }) {
  return children;
}
