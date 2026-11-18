// What the sign-in page shows besides the form (#457 review): the providers switched on (GET
// /auth/session, signed out) and whether email is on (GET /auth/email, a 404 while off). The
// server page asks before it is drawn, so "Continue with Google" and "Forgot password?" never
// move the form once it shows. No answer in time: neither, as when both are off. The browser
// never asks for these.

// The backend, as next.config.ts's /api rewrite reaches it.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";
const WAIT_MS = 2000;

export interface SignInOptions {
  providers: string[];
  emailOn: boolean;
  keycloakEnabled: boolean;
}

/** B19: the visitor's IP, the first address of the incoming X-Forwarded-For (Caddy sets it); null without one. */
export function visitorIp(headers: Pick<Headers, "get">): string | null {
  return headers.get("x-forwarded-for")?.split(",")[0]?.trim() || null;
}

/** The reply's JSON, or null for a refusal (email off: 404), no answer or a slow one. */
async function ask(path: string, get: typeof fetch, visitor: string | null): Promise<Record<string, unknown> | null> {
  try {
    // B19: the backend counts these calls per visitor (A7), not all as the frontend's own address.
    const headers: Record<string, string> = visitor ? { "X-Forwarded-For": visitor } : {};
    const reply = await get(`${backend}/api${path}`, { cache: "no-store", headers, signal: AbortSignal.timeout(WAIT_MS) });
    if (reply.ok) return (await reply.json()) as Record<string, unknown>;
    // Email off is a 404, an answer; anything else is logged on the server, since the page then
    // quietly shows the email and password form alone (review, stability pass).
    if (reply.status !== 404) console.warn(`Sign-in options: ${path} answered ${reply.status}`);
    return null;
  } catch (caught) {
    console.warn(`Sign-in options: ${path} did not answer:`, caught);
    return null; // no answer: the email and password form alone, as with both off
  }
}

export async function signInOptions(get: typeof fetch = fetch, visitor: string | null = null): Promise<SignInOptions> {
  const [session, email] = await Promise.all([ask("/auth/session", get, visitor), ask("/auth/email", get, visitor)]);
  const providers = session?.providers;
  return {
    providers: Array.isArray(providers) ? providers.filter((name): name is string => typeof name === "string") : [],
    emailOn: email?.enabled === true,
    // Fail closed until the backend advertises the public availability flag.
    keycloakEnabled: session?.keycloak_enabled === true,
  };
}
