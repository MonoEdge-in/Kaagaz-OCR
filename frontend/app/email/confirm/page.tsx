// /email/confirm#t=... (#457 phase 0): the link from "Confirm your email". Outside the (app)
// group: a new organisation's admin cannot sign in yet. The token is in the fragment.

import { AuthFrame } from "../../login/AuthFrame";
import { ConfirmEmail } from "./ConfirmEmail";

export const metadata = { title: "Confirm your email" };

export default function ConfirmEmailPage() {
  return (
    <AuthFrame>
      <ConfirmEmail />
    </AuthFrame>
  );
}
