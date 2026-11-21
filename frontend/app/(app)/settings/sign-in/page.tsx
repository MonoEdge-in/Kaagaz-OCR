// /settings/sign-in: Account, Ways to sign in (#457 phase 1), from the account menu. Google's
// callback comes back here with ?connected=, ?waiting= or ?error=.

import { WaysIntro, WaysToSignIn } from "@/components/account/WaysToSignIn";
import { TITLE } from "@/components/account/signInWays";
import { safeNextPath } from "@/components/shell/gate";

export const metadata = { title: TITLE };

export default async function WaysToSignInPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const params: Record<string, string | undefined> = {};
  for (const key of ["connected", "waiting", "error", "keycloak_error", "keycloak"]) {
    const value = query[key];
    if (typeof value === "string") params[key] = value;
  }
  const next = safeNextPath(query.next) ?? "/dashboard";
  return (
    <div className="page">
      <header className="page-header">
        <h1>{TITLE}</h1>
        <WaysIntro />
      </header>
      <WaysToSignIn params={params} next={next} />
    </div>
  );
}
