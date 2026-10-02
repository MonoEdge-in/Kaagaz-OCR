import type { NextConfig } from "next";

// The browser only ever talks to this app. Every /api call is forwarded to the
// FastAPI backend, so the session cookie stays first-party and no CORS is needed.
const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
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
