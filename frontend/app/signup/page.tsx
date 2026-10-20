// /signup: anyone creates an organisation and becomes its first admin (16 Oct
// 2026). Outside the (app) group, like the sign-ins: nobody is signed in yet.
// The same sign-in frame (AuthFrame). With sign-up switched off on the server
// the sign-in pages hide their link, and a send here says it is off.

import { AuthFrame } from "../login/AuthFrame";
import { SignUpForm } from "./SignUpForm";

export const metadata = { title: "Create an account" };

export default function SignUpPage() {
  return (
    <AuthFrame>
      <SignUpForm />
    </AuthFrame>
  );
}
