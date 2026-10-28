// The sign-in page, the one sign-in for everyone (16 Oct 2026), in the shared
// sign-in frame (AuthFrame). The backend gives the same short message for an
// unknown e-mail, a wrong password or a disabled account, and the form shows it
// as is; an admin also gives the 6-digit code, or sets two-step sign-in up first.
// A link to /signup while sign-up is on (SignUpLink). After signing out, or a
// session ending, a note under the form's heading says so (SignedOutNotice). While
// sign-in is through Keycloak (#435), "Sign in with Keycloak" takes the form's place
// (SignInChoice).

import { pathAfterSignIn, signedOutMessage } from "@/components/shell/gate";
import { AuthFrame } from "./AuthFrame";
import { SignInChoice } from "./KeycloakSignIn";
import { keycloakProblem } from "./signIn";
import { SignUpLink } from "./SignUpLink";

export const metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  return (
    <AuthFrame>
      <SignInChoice note={signedOutMessage(query)} next={pathAfterSignIn(query)} problem={keycloakProblem(query)} />
      <SignUpLink />
    </AuthFrame>
  );
}
