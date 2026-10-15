// /operator/login: the platform operator's (super admin's) sign-in. Outside
// app/(app) on purpose: that group's SessionProvider is for customer users,
// and an operator belongs to no organisation. The same sign-in frame as
// /login, with "Kaagaz · Platform" as the panel's wordmark.

import { signedOutMessage } from "@/components/shell/gate";
import { AuthFrame } from "../../login/AuthFrame";
import { OperatorSignInForm } from "./OperatorSignInForm";

export default async function OperatorLoginPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const note = signedOutMessage(await searchParams);
  return (
    <AuthFrame variant="platform">
      <OperatorSignInForm note={note} />
    </AuthFrame>
  );
}
