// The operator (super admin) screens. OperatorSessionProvider asks the backend
// who is signed in (GET /api/operator/me) and sends the browser to
// /operator/login when nobody is; OperatorShell draws the top bar. The
// sign-in page sits outside this group, so it gets neither.

import type { ReactNode } from "react";
import { OperatorShell } from "@/components/operator/OperatorShell";
import { OperatorSessionProvider } from "@/lib/operatorSession";

export default function OperatorLayout({ children }: { children: ReactNode }) {
  return (
    <OperatorSessionProvider>
      <OperatorShell>{children}</OperatorShell>
    </OperatorSessionProvider>
  );
}
