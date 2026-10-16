// /admin/login: the separate sign-in for customer admins (PRD A1). It sits
// outside the (app) group on purpose: nobody is signed in yet, so there is no
// session to load and no shell. The same sign-in frame as /login (AuthFrame).
// The form gives an admin session, which alone opens /admin. A note says when
// a session has just ended; a link to /signup while sign-up is on.

import { signedOutMessage } from "@/components/shell/gate";
import { AuthFrame } from "../../login/AuthFrame";
import { SignUpLink } from "../../login/SignUpLink";
import { AdminSignInForm } from "./AdminSignInForm";

export const metadata = { title: "Sign in" };

export default async function AdminLoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const note = signedOutMessage(await searchParams);
  return (
    <AuthFrame variant="admin">
      <AdminSignInForm note={note} />
      <SignUpLink />
    </AuthFrame>
  );
}
