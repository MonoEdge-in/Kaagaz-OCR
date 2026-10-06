// Critical #5: the browser keeps no copy of an app page (no-store), so the
// back button after signing out cannot show a signed-in screen from its
// cache. The rules are matched the way Next.js matches them.

import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match";
import { describe, expect, it } from "vitest";
import nextConfig from "@/next.config";

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

  it("keep the security headers everywhere", async () => {
    for (const path of ["/documents", "/api/documents"]) {
      expect(await headersFor(path), path).toMatchObject({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
      });
    }
  });
});
