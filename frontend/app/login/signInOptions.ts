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
}

/** The reply's JSON, or null for a refusal (email off: 404), no answer or a slow one. */
async function ask(path: string, get: typeof fetch): Promise<Record<string, unknown> | null> {
  try {
    const reply = await get(`${backend}/api${path}`, { cache: "no-store", signal: AbortSignal.timeout(WAIT_MS) });
    return reply.ok ? ((await reply.json()) as Record<string, unknown>) : null;
  } catch {
    return null; // no answer: the email and password form alone, as with both off
  }
}

export async function signInOptions(get: typeof fetch = fetch): Promise<SignInOptions> {
  const [session, email] = await Promise.all([ask("/auth/session", get), ask("/auth/email", get)]);
  const providers = session?.providers;
  return {
    providers: Array.isArray(providers) ? providers.filter((name): name is string => typeof name === "string") : [],
    emailOn: email?.enabled === true,
  };
}
