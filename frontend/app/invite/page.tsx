// /invite#t=... (#457 phase 0): the link from "You're invited". Outside the (app) group:
// nobody is signed in yet. The token is in the fragment, never sent to a server.

import { AuthFrame } from "../login/AuthFrame";
import { LinkPasswordForm } from "../reset-password/LinkPasswordForm";

export const metadata = { title: "Choose your password" };

export default function InvitePage() {
  return (
    <AuthFrame>
      <LinkPasswordForm kind="invite" />
    </AuthFrame>
  );
}
