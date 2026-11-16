// PRD 7 PR 11b (#604, PR 10c): a confirmed keep-delete sends the admin's password in the DELETE's
// JSON body, never the address. The browser reaches the backend through next.config.ts's /api
// rewrite, which Next serves with its proxyRequest (the one the router server calls); this sends a
// DELETE with a body through that same function to a small backend, which must get it whole.

import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import nextConfig from "../next.config";

describe("the /api rewrite", () => {
  it("forwards every /api path to the backend's /api, whatever the method", async () => {
    const rewrites = await nextConfig.rewrites?.();
    const list = Array.isArray(rewrites) ? rewrites : (rewrites?.afterFiles ?? []);
    const rule = list.find((each) => each.source === "/api/:path*");
    expect(rule?.destination).toMatch(/\/api\/:path\*$/);
    expect(rule).not.toHaveProperty("has"); // no method or header condition: DELETE goes too
  });

  it("hands a DELETE's JSON body to the backend unchanged", async () => {
    const { proxyRequest } = await import("next/dist/server/lib/router-utils/proxy-request");
    const got: { method?: string; url?: string; body?: string } = {};
    const backend = createServer((req, res) => {
      let body = "";
      req.on("data", (chunk) => (body += chunk));
      req.on("end", () => {
        Object.assign(got, { method: req.method, url: req.url, body });
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end('{"files": 3, "folders": 0}');
      });
    });
    await new Promise<void>((resolve) => backend.listen(0, "127.0.0.1", resolve));
    const port = (backend.address() as AddressInfo).port;
    const front = createServer((req, res) => {
      const [pathname, search = ""] = (req.url ?? "/").split("?");
      const query = Object.fromEntries(new URLSearchParams(search));
      const target = { protocol: "http:", hostname: "127.0.0.1", port: String(port), pathname: pathname ?? "/", query };
      void proxyRequest(req, res, target as unknown as Parameters<typeof proxyRequest>[2], undefined, req, 5000);
    });
    await new Promise<void>((resolve) => front.listen(0, "127.0.0.1", resolve));
    try {
      const at = (front.address() as AddressInfo).port;
      const reply = await fetch(`http://127.0.0.1:${at}/api/folders/f?contents=keep&confirm=true`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: "pw-typed" }),
      });
      expect(reply.status).toBe(200);
      expect(got).toEqual({
        method: "DELETE",
        url: "/api/folders/f?contents=keep&confirm=true",
        body: '{"password":"pw-typed"}',
      });
    } finally {
      front.close();
      backend.close();
    }
  });
});
