// Critical #5: the browser keeps no copy of an app page (no-store), so the
// back button after signing out cannot show a signed-in screen from its
// cache. The rules are matched the way Next.js matches them.

import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match";
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import nextConfig from "@/next.config";
import { proxy } from "@/proxy";

async function headersFor(path: string): Promise<Record<string, string>> {
  const out: Record<string, string> = {};
  for (const rule of (await nextConfig.headers?.()) ?? []) {
    const match = getPathMatch(rule.source, { strict: true, removeUnnamedParams: true });
    if (match(path) === false) continue;
    for (const { key, value } of rule.headers) out[key] = value;
  }
  return out;
}

describe("response headers", () => {
  it("say no-store on every app page", async () => {
    for (const path of ["/", "/login", "/admin/login", "/documents", "/documents/d-1", "/chat", "/admin", "/operator", "/operator/login"]) {
      expect((await headersFor(path))["Cache-Control"], path).toBe("no-store");
    }
  });

  it("leave Next.js's own files and the API to set their own caching", async () => {
    for (const path of ["/_next/static/chunks/main.js", "/_next/image", "/api/documents/d-1/file"]) {
      expect((await headersFor(path))["Cache-Control"], path).toBeUndefined();
    }
  });

  it("send no referrer from the emailed links' pages (#457)", async () => {
    for (const path of ["/reset-password", "/invite", "/email/confirm"]) {
      expect((await headersFor(path))["Referrer-Policy"], path).toBe("no-referrer");
    }
    expect((await headersFor("/documents"))["Referrer-Policy"]).toBe("same-origin");
  });

  it("keep the security headers everywhere", async () => {
    for (const path of ["/documents", "/api/documents"]) {
      expect(await headersFor(path), path).toMatchObject({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
      });
    }
  });

  it("report a content security policy without enforcing it, and switch off camera, microphone and location", async () => {
    // The policy comes from proxy.ts on every page (#481, 3 Nov 2026); see csp-nonce.test.ts.
    for (const path of ["/documents/d-1", "/login", "/operator"]) {
      const headers = proxy(new NextRequest(new URL(path, "https://kaagaz.example"))).headers;
      expect(headers.get("Content-Security-Policy"), path).toBeNull();
      const policy = headers.get("Content-Security-Policy-Report-Only");
      for (const rule of ["default-src 'self'", "worker-src 'self' blob:", "object-src 'none'", "frame-ancestors 'none'", "connect-src 'self'"]) {
        expect(policy, path).toContain(rule);
      }
    }
    for (const path of ["/documents/d-1", "/_next/static/chunks/main.js", "/api/documents"]) {
      expect((await headersFor(path))["Permissions-Policy"], path).toBe("camera=(), microphone=(), geolocation=()");
    }
  });
});
