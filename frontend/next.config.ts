import type { NextConfig } from "next";

// The browser only ever talks to this app. Every /api call is forwarded to the
// FastAPI backend, so the session cookie stays first-party and no CORS is needed.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  logging: {
    incomingRequests: {
      ignore: [/\/api\/auth\/keycloak(?:\/|$)/],
    },
  },
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
          // The content security policy is set by proxy.ts: its script rule carries a fresh
          // nonce on every page (#481, 3 Nov 2026).
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
      {
        // The emailed links' pages (#457): their token is in the fragment, never sent anyway;
        // no referrer at all leaves these pages, as the spec asks for link paths.
        source: "/email/confirm",
        headers: [{ key: "Referrer-Policy", value: "no-referrer" }],
      },

      {
        // Keycloak callbacks carry one-time authorization data in their query string.
        source: "/api/auth/keycloak/:path*",
        headers: [{ key: "Referrer-Policy", value: "no-referrer" }],
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
