// The sign-in page (21 Nov 2026): one screen, one button. Keycloak is the only sign-in for
// company users, so "Sign in" is a plain link to the API's start (a GET that goes on to
// Keycloak), keeping where the person was going. After signing out, or a session ending, a note
// under the heading says so (SignedOutNotice); a sign-in that did not finish comes back as
// ?keycloak_error=<code> and is said above the button (keycloakCallbackError).

import { pathAfterSignIn, signedOutMessage } from "@/components/shell/gate";
import { AuthFrame } from "./AuthFrame";
import { SignInPanel } from "./SignInPanel";

export const metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  return (
    <AuthFrame>
      <SignInPanel
        note={signedOutMessage(query)}
        next={pathAfterSignIn(query)}
        keycloakError={typeof query.keycloak_error === "string" ? query.keycloak_error : null}
      />
    </AuthFrame>
  );
}
