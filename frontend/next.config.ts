import type { NextConfig } from "next";

// The browser only ever talks to this app. Every /api call is forwarded to the
// FastAPI backend, so the session cookie stays first-party and no CORS is needed.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

// Everything comes from this app: Next.js's inline scripts and styles, the
// PDF.js worker (a file under /_next, or a blob: one), page images drawn from
// data: and blob: URLs, and PDF.js's fonts from data: URLs. The PDF viewer's
// engine is WebAssembly (served from /_next, compiled under 'wasm-unsafe-eval',
// which allows no JavaScript eval) and Print frames the file from a blob: URL.
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "worker-src 'self' blob:",
  "img-src 'self' data: blob:",
  "frame-src 'self' blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  experimental: {
    // How long the /api rewrite waits for the backend. Next's default is 30 s,
    // and 0 does not switch it off (it means the default): a chat answer on a
    // CPU makes up to 5 model calls of MODEL_TIMEOUT_SECONDS (60 s) each, so it
    // was cut at 30 s and looked failed while the server went on to save it.
    // 5 x 60 s plus 30 s of margin (council 3 Oct 2026, "story search mode").
    proxyTimeout: 330_000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          // Security checklist (15 Oct 2026): report only, so the browser logs what
          // would be blocked and blocks nothing, until it is checked on the live site.
          { key: "Content-Security-Policy-Report-Only", value: CSP },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
      {
        // The browser keeps no copy of an app page, so after signing out the
        // back button cannot show a signed-in screen from its cache. Not for
        // /_next/ (Next.js's hashed files, kept for good) or /api (the
        // backend sets its own).
        source: "/((?!_next/|api/).*)",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
    ];
  },
};

export default nextConfig;
