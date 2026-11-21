import { type NextRequest, NextResponse } from "next/server";

// The content security policy, set here rather than in next.config.ts because its script rule
// carries a fresh nonce on every page (#481, 3 Nov 2026): Next.js reads the nonce from the
// request's policy and puts it on its own scripts, so 'unsafe-inline' is no longer needed for
// scripts. 'strict-dynamic' lets a script that carries the nonce load others: Next.js's chunks,
// and Google's script, which components/analytics/ga.ts adds from one. Pages already render per
// request (app/layout.tsx reads the theme cookie), so the nonce costs no static page.
//
// Everything else comes from this app: Next.js's inline styles, the PDF.js worker (a file under
// /_next, or a blob: one), page images drawn from data: and blob: URLs, and PDF.js's fonts from
// data: URLs. The PDF viewer's engine is WebAssembly (served from /_next, compiled under
// 'wasm-unsafe-eval', which allows no JavaScript eval) and Print frames the file from a blob: URL.
// Google Analytics (#457 phase 4) adds Google's script, and the hosts it sends page views to,
// on the public pages that load it only, never app-wide.
function csp(nonce: string, script = "", collect = ""): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'wasm-unsafe-eval'${script}`,
    "style-src 'self' 'unsafe-inline'",
    "worker-src 'self' blob:",
    `img-src 'self' data: blob:${collect}`,
    "frame-src 'self' blob:",
    "font-src 'self' data:",
    `connect-src 'self'${collect}`,
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");
}

// The pages components/analytics/ga.ts tracks (PAGE_TITLES); not while ?step=code.
const GA_PAGES = new Set(["/login"]);

// Security checklist (15 Oct 2026): report only, so the browser logs what would be blocked and
// blocks nothing, until it is checked on the live site.
const HEADER = "Content-Security-Policy-Report-Only";

export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const { pathname, searchParams } = request.nextUrl;
  const policy =
    GA_PAGES.has(pathname) && searchParams.get("step") !== "code"
      ? csp(
          nonce,
          " https://www.googletagmanager.com",
          " https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com",
        )
      : csp(nonce);
  // On the request for Next.js's render to find the nonce, and on the response for the browser.
  const headers = new Headers(request.headers);
  headers.set(HEADER, policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set(HEADER, policy);
  return response;
}

// Pages only: not Next.js's hashed files and images, the favicon, or /api, which
// next.config.ts rewrites to the backend untouched.
export const config = {
  matcher: "/((?!api|_next/static|_next/image|favicon.ico).*)",
};
