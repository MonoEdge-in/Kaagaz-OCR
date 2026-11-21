// The company sign-in step for a risky access change (#737). The server answers 403
// keycloak_reauth_required when the change needs a fresh sign-in: `run` then opens the company
// sign-in page and resolves null (the person repeats the change on coming back); any other
// failure goes to the caller. Tested in tests/access-proof-step.test.ts.

import { useCallback } from "react";
import { beginKeycloakReauth, isKeycloakReauthRequired, keycloakStartFailure } from "@/lib/keycloak";

/** Sends `send`; resolves with its reply, or null when the company sign-in was started instead.
 * `path` (the page to come back to) is read only when the sign-in starts. */
export async function withProof<T>(
  send: () => Promise<T>,
  path: () => string = () => window.location.pathname,
): Promise<T | null> {
  try {
    return await send();
  } catch (caught) {
    if (!isKeycloakReauthRequired(caught)) throw caught;
    try {
      await beginKeycloakReauth("access", path());
      return null;
    } catch (failed) {
      throw new Error(keycloakStartFailure(failed));
    }
  }
}

export function useProofStep() {
  const run = useCallback(<T,>(send: () => Promise<T>) => withProof(send), []);
  return { run };
}
