// /forgot-password (#457 phase 0): "Forgot password?" from the sign-in page. Outside the
// (app) group, like the sign-ins: nobody is signed in. The same sign-in frame (AuthFrame).

import { AuthFrame } from "../login/AuthFrame";
import { ForgotForm } from "./ForgotForm";

export const metadata = { title: "Forgot password" };

export default function ForgotPasswordPage() {
  return (
    <AuthFrame>
      <ForgotForm />
    </AuthFrame>
  );
}
