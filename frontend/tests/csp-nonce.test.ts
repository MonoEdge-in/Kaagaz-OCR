// The content security policy's script rule (#481, 3 Nov 2026): a fresh nonce on every page
// instead of 'unsafe-inline', set by proxy.ts. Still report only (security checklist, 15 Oct
// 2026), and still never on Next.js's own files or the /api calls the backend answers.

import { unstable_doesMiddlewareMatch } from "next/experimental/testing/server";
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import nextConfig from "@/next.config";
import { config, proxy } from "@/proxy";

const HEADER = "Content-Security-Policy-Report-Only";

function pageFor(address: string) {
  return proxy(new NextRequest(new URL(address, "https://kaagaz.example")));
}

function scriptRule(policy: string): string {
  return policy.split("; ").find((part) => part.startsWith("script-src ")) ?? "";
}

function nonceIn(policy: string): string {
  return /'nonce-([A-Za-z0-9+/=]+)'/.exec(scriptRule(policy))?.[1] ?? "";
}

describe("the script rule's nonce", () => {
  it("allows scripts by a nonce, never 'unsafe-inline', and keeps WebAssembly for the PDF viewer", () => {
    for (const path of ["/documents/d-1", "/login", "/login?step=code", "/admin", "/operator"]) {
      const rule = scriptRule(pageFor(path).headers.get(HEADER) ?? "");
      expect(rule, path).toMatch(/^script-src 'self' 'nonce-[A-Za-z0-9+/=]{16,}' 'strict-dynamic' 'wasm-unsafe-eval'/);
      expect(rule, path).not.toContain("'unsafe-inline'");
      expect(rule, path).not.toContain("'unsafe-eval'");
    }
  });

  it("is new on every request", () => {
    const nonces = new Set(Array.from({ length: 20 }, () => nonceIn(pageFor("/documents").headers.get(HEADER) ?? "")));
    expect(nonces.size).toBe(20);
    expect(nonces.has("")).toBe(false);
  });

  it("reaches Next.js's render through the request, so its own scripts carry the same nonce", () => {
    const response = pageFor("/documents");
    const policy = response.headers.get(HEADER) ?? "";
    // NextResponse.next({ request: { headers } }) hands the page these request headers.
    expect(response.headers.get(`x-middleware-request-${HEADER.toLowerCase()}`)).toBe(policy);
    expect(nonceIn(policy)).not.toBe("");
  });

  it("is still report only: nothing is enforced", () => {
    expect(pageFor("/documents").headers.get("Content-Security-Policy")).toBeNull();
  });
});

describe("where the proxy runs", () => {
  const runs = (url: string) => unstable_doesMiddlewareMatch({ config, nextConfig, url });

  it("runs on every page", () => {
    for (const url of ["/", "/login", "/login?step=code", "/documents/d-1", "/chat", "/admin", "/operator/login"]) {
      expect(runs(url), url).toBe(true);
    }
  });

  it("never on Next.js's files, the favicon or the /api rewrite to the backend", () => {
    for (const url of ["/_next/static/chunks/main.js", "/_next/image?url=x", "/favicon.ico", "/api/documents", "/api/auth/oidc/google/callback"]) {
      expect(runs(url), url).toBe(false);
    }
  });

  it("leaves the policy to the proxy alone: next.config.ts sets none", async () => {
    for (const rule of (await nextConfig.headers?.()) ?? []) {
      for (const { key } of rule.headers) expect(key, rule.source).not.toMatch(/^Content-Security-Policy/);
    }
  });
});
