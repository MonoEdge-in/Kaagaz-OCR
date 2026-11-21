// #737: the company sign-in step for a risky access change. The change is sent first; a 403
// keycloak_reauth_required starts the company sign-in and resolves null (nothing more is sent);
// any other failure goes to the caller. No password or code is asked for here.

import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { withProof } from "@/components/access/proofStep";
import { ApiError } from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

const REAUTH = new ApiError(403, "keycloak_reauth_required", "Sign in again to go on.");

function company(start: "ok" | "limited" = "ok") {
  const assign = vi.fn();
  const calls: string[] = [];
  vi.stubGlobal("window", { location: { origin: "https://kaagaz.example", assign } });
  vi.stubGlobal("fetch", async (url: string) => {
    calls.push(url);
    if (start === "limited") return new Response(JSON.stringify({ error: { code: "rate_limited", message: "x" } }), { status: 429 });
    return new Response(JSON.stringify({ url: "https://idp.example/authorize" }), { status: 200 });
  });
  return { assign, calls };
}

describe("withProof", () => {
  it("returns the reply when the change goes through, and starts nothing", async () => {
    const sign = company();
    const send = vi.fn(async () => "done");
    expect(await withProof(send, () => "/admin")).toBe("done");
    expect(send).toHaveBeenCalledTimes(1);
    expect(sign.calls).toEqual([]);
  });

  it("starts the company sign-in on 403 keycloak_reauth_required and sends nothing again", async () => {
    const sign = company();
    const send = vi.fn(async () => {
      throw REAUTH;
    });
    expect(await withProof(send, () => "/admin?tab=access")).toBeNull();
    expect(send).toHaveBeenCalledTimes(1);
    expect(sign.calls).toEqual(["/api/auth/keycloak/reauth/access/start"]);
    expect(sign.assign).toHaveBeenCalledWith("https://idp.example/authorize");
  });

  it("says why the sign-in could not start", async () => {
    company("limited");
    await expect(
      withProof(async () => {
        throw REAUTH;
      }, () => "/admin"),
    ).rejects.toThrow("Too many company sign-in checks were started. Wait a minute, then try again.");
  });

  it("hands any other failure to the caller", async () => {
    const sign = company();
    const refused = new ApiError(409, "disabled", "This person is switched off.");
    await expect(
      withProof(async () => {
        throw refused;
      }, () => "/admin"),
    ).rejects.toBe(refused);
    expect(sign.calls).toEqual([]);
  });
});

describe("no password or code is collected for a company user", () => {
  const source = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

  it("has no password step or box left in the access, lock, version and WhatsApp screens", () => {
    for (const path of [
      "components/access/AudiencePanel.tsx",
      "components/access/GiveAccessDialog.tsx",
      "components/access/KeptFromMe.tsx",
      "components/admin/AccessPage.tsx",
      "components/admin/DepartmentsPanel.tsx",
      "components/admin/UsersPanel.tsx",
      "components/admin/WhatsAppPanel.tsx",
      "components/documents/DeleteFolderDialog.tsx",
      "components/documents/FolderActions.tsx",
      "components/documents/LockDialog.tsx",
      "components/versions/LinkDialog.tsx",
      "components/versions/VersionsPanel.tsx",
    ]) {
      const text = source(path);
      expect(text, path).not.toContain("PasswordField");
      expect(text, path).not.toContain("usePasswordStep");
      expect(text, path).not.toMatch(/autoComplete="current-password"/);
    }
  });
});
