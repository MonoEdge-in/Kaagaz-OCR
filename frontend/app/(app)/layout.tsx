"use client";

// Every signed-in screen sits under this layout. SessionProvider asks the
// backend who is signed in (redirecting to /login when nobody is) and shows a
// loading state until it knows; AppShell draws the top bar.

import type { ReactNode } from "react";
import { AppShell } from "@/components/shell/AppShell";
import { SessionProvider } from "@/lib/session";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <SessionProvider>
      <AppShell>{children}</AppShell>
    </SessionProvider>
  );
}
