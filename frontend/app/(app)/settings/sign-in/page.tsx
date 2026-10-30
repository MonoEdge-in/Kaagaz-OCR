// /settings/sign-in: Account, Ways to sign in (#457 phase 1), from the account menu. Google's
// callback comes back here with ?connected=, ?waiting= or ?error=.

import { WaysToSignIn } from "@/components/account/WaysToSignIn";
import { INTRO, TITLE } from "@/components/account/signInWays";

export const metadata = { title: TITLE };

export default async function WaysToSignInPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const params: Record<string, string | undefined> = {};
  for (const key of ["connected", "waiting", "error"]) {
    const value = query[key];
    if (typeof value === "string") params[key] = value;
  }
  return (
    <div className="page">
      <header className="page-header">
        <h1>{TITLE}</h1>
        <p className="hint">{INTRO}</p>
      </header>
      <WaysToSignIn params={params} />
    </div>
  );
}
