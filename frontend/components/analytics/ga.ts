// Google Analytics on the public pages only, after the visitor accepts it (#457 phase 4).
// Inside the app it would send page titles (document names), addresses (document ids) and
// searched words, so it never runs there. Nor on the pages that hold a secret while open: the
// emailed links' pages (a token, a new password) and /login?step=code (the code after Google).
// What it sends: the page's path, without its query string or fragment, and a fixed title.
// Never what is typed, never a user or organisation id. No measurement id, no analytics.

export type Consent = "accepted" | "declined";
export type View = "off" | "ask" | "on";

export const CONSENT_KEY = "kaagaz.analytics";

export const BANNER_TEXT =
  "We'd like to use Google Analytics to see how people find the sign-in pages. Nothing you type is sent.";

// The public pages and their fixed titles. A page not here sends nothing: a public page
// added later gets its line here, unless it holds a token, a password or a code.
export const PAGE_TITLES: Readonly<Record<string, string>> = {
  "/login": "Sign in",
};

// NEXT_PUBLIC_GA_ID, when it is a GA4 measurement id ("G-" then letters and digits).
// Anything else counts as unset.
export function gaId(raw: string | undefined): string | null {
  const id = raw?.trim() ?? "";
  return /^G-[A-Za-z0-9]+$/.test(id) ? id : null;
}

// The path alone: "/email/confirm#t=..." is "/email/confirm".
export function pagePath(path: string): string {
  return path.split(/[?#]/, 1)[0] || "/";
}

// The fixed title of a tracked page, from its path and query: none for a page not in
// PAGE_TITLES, and none for /login while it asks for the code after Google (?step=code).
function titleFor(path: string): string | null {
  const url = new URL(path, "http://x");
  if (url.pathname === "/login" && url.searchParams.get("step") === "code") return null;
  return PAGE_TITLES[pagePath(path)] ?? null;
}

// path: the page's path with its query string, which the code step needs.
export function viewFor(id: string | null, path: string, consent: Consent | null): View {
  if (!id || !titleFor(path) || consent === "declined") return "off";
  return consent === "accepted" ? "on" : "ask";
}

// The choice, kept in localStorage. Blocked storage (private mode) keeps nothing: the
// banner simply asks again.
export function readConsent(storage: () => Storage): Consent | null {
  try {
    const value = storage().getItem(CONSENT_KEY);
    return value === "accepted" || value === "declined" ? value : null;
  } catch {
    return null;
  }
}

export function saveConsent(storage: () => Storage, consent: Consent): void {
  try {
    storage().setItem(CONSENT_KEY, consent);
  } catch {
    // not kept: the banner asks again next time
  }
}

type Gtag = (...args: unknown[]) => void;

export interface GtagWindow {
  dataLayer?: unknown[];
  gtag?: Gtag;
  location: { origin: string };
}

export type GtagDocument = Pick<Document, "referrer" | "head" | "createElement">;

// Where the visitor came from, as its origin only: an app page's address holds document ids.
function referrerOrigin(referrer: string): string {
  try {
    return referrer ? `${new URL(referrer).origin}/` : "";
  } catch {
    return "";
  }
}

// Adds Google's script once, then sends this page's one page view. The page is set first for
// everything gtag sends, so Google's script never reads the address bar's query string. The
// config sends no page view of its own and has Google signals and ad personalisation off.
export function startGtag(win: GtagWindow, doc: GtagDocument, id: string, path: string): void {
  const page = pagePath(path);
  const title = titleFor(path);
  if (!title) return;
  if (!win.gtag) {
    const layer = (win.dataLayer ??= []);
    // gtag queues its arguments object itself, not an array: Google's script expects that.
    win.gtag = function gtag() {
      // eslint-disable-next-line prefer-rest-params
      layer.push(arguments);
    };
    win.gtag("js", new Date());
    const script = doc.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${id}`;
    doc.head.appendChild(script);
  }
  const view = {
    page_location: win.location.origin + page,
    page_title: title,
    page_referrer: referrerOrigin(doc.referrer),
  };
  win.gtag("set", view);
  win.gtag("config", id, {
    send_page_view: false,
    allow_google_signals: false,
    allow_ad_personalization_signals: false,
    ...view,
  });
  win.gtag("event", "page_view", view);
}
