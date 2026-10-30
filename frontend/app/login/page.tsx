// The sign-in page, the one sign-in for everyone (16 Oct 2026), in the shared
// sign-in frame (AuthFrame). The backend gives the same short message for an
// unknown e-mail, a wrong password or a disabled account, and the form shows it
// as is; an admin also gives the 6-digit code, or sets two-step sign-in up first.
// A link to /signup while sign-up is on (SignUpLink). After signing out, or a
// session ending, a note under the form's heading says so (SignedOutNotice).
// Ways to sign in (#457 phase 1): "Continue with Google" above the form while it is on, a
// failed provider sign-in's ?oidc_error=<code>, and ?step=code, the code after Google.

import { pathAfterSignIn, signedOutMessage } from "@/components/shell/gate";
import { AuthFrame } from "./AuthFrame";
import { SignInForm } from "./SignInForm";
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
      <SignInForm
        note={signedOutMessage(query)}
        next={pathAfterSignIn(query)}
        oidcError={typeof query.oidc_error === "string" ? query.oidc_error : null}
        providerStep={query.step === "code"}
      />
      <SignUpLink />
    </AuthFrame>
  );
}
