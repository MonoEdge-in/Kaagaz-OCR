// #737: signing out also ends company sign-in. The page asks the server for Keycloak's sign-out
// address (GET, no body) and leaves for it.

import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

describe("keycloakLogoutUrl", () => {
  it("asks GET /api/auth/keycloak/logout-url and returns the address", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url}`);
      return new Response(JSON.stringify({ url: "https://id.example.test/logout?client_id=a" }), { status: 200 });
    });
    expect(await api.keycloakLogoutUrl()).toEqual({ url: "https://id.example.test/logout?client_id=a" });
    expect(calls).toEqual(["GET /api/auth/keycloak/logout-url"]);
  });
});
