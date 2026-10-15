import type { ReactNode } from "react";

// The tab's title: the section's name only, never a document's (app/layout.tsx).
export const metadata = { title: "Documents" };

export default function DocumentsLayout({ children }: { children: ReactNode }) {
  return children;
}
