// /reset-password#t=... (#457 phase 0): the link from "Reset your password". Outside the
// (app) group: nobody is signed in. The token is in the fragment, never sent to a server.

import { AuthFrame } from "../login/AuthFrame";
import { LinkPasswordForm } from "./LinkPasswordForm";

export const metadata = { title: "Choose a new password" };

export default function ResetPasswordPage() {
  return (
    <AuthFrame>
      <LinkPasswordForm kind="reset" />
    </AuthFrame>
  );
}
