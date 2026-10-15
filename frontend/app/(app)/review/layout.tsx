import type { ReactNode } from "react";

// The tab's title: the section's name only, never a document's (app/layout.tsx).
export const metadata = { title: "Review" };

export default function ReviewLayout({ children }: { children: ReactNode }) {
  return children;
}
