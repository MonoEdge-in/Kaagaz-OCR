// /settings/whatsapp: Account, WhatsApp (#603), from the account menu while the server has WhatsApp
// on. Google's callback comes back here with ?proved=google or ?error=.

import { WhatsAppConnect } from "@/components/account/WhatsAppConnect";
import { TITLE } from "@/components/account/whatsapp";

export const metadata = { title: TITLE };

export default async function WhatsAppPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const query = await searchParams;
  const params: Record<string, string | undefined> = {};
  for (const key of ["proved", "error", "reauth"]) {
    const value = query[key];
    if (typeof value === "string") params[key] = value;
  }
  return (
    <div className="page">
      <header className="page-header">
        <h1>{TITLE}</h1>
      </header>
      <WhatsAppConnect params={params} />
    </div>
  );
}
