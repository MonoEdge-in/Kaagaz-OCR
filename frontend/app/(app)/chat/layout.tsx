import type { ReactNode } from "react";

// The tab's title: the section's name only, never a document's (app/layout.tsx).
export const metadata = { title: "Ask" };

export default function ChatLayout({ children }: { children: ReactNode }) {
  return children;
}
