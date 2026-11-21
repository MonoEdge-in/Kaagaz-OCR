import { afterEach, describe, expect, it, vi } from "vitest";
import {
  getUploadLimits,
  logoutEverywhere,
  onSessionEnded,
  query,
  resetUser2fa,
  setDeleteForever,
  unlockUser,
} from "@/lib/api";
import type { AdminUser } from "@/lib/types";

describe("query", () => {
  it("is empty when nothing is set", () => {
    expect(query({})).toBe("");
    expect(query({ q: "", status: undefined })).toBe("");
    expect(query({ q: "", tag_id: [], folder_id: undefined })).toBe("");
  });

  it("keeps strings and numbers as before, zero included", () => {
    expect(query({ limit: 50, offset: 0, q: "GST 2026/27" })).toBe(
      "?limit=50&offset=0&q=GST+2026%2F27",
    );
  });

  it("repeats the key once for each item of an array", () => {
    expect(query({ tag_id: ["t1", "t2", "t3"] })).toBe("?tag_id=t1&tag_id=t2&tag_id=t3");
  });

  it("sends nothing for an empty array or an empty item", () => {
    expect(query({ tag_id: [] })).toBe("");
    expect(query({ tag_id: ["", "t2"] })).toBe("?tag_id=t2");
  });

  it("writes booleans as true and false, and keeps false", () => {
    expect(query({ include_subfolders: true, trashed: false })).toBe(
      "?include_subfolders=true&trashed=false",
    );
  });

  it("encodes what was typed", () => {
    expect(query({ q: "GST & co" })).toBe("?q=GST+%26+co");
  });

  it("keeps the order the keys were given in", () => {
    expect(query({ folder_id: "f1", tag_id: ["a", "b"], limit: 20 })).toBe(
      "?folder_id=f1&tag_id=a&tag_id=b&limit=20",
    );
  });
});

// The sign-in and admin calls, checked against the contract (plan 4 WP1.3a and
// 9.5): the method, the path, the CSRF header and the exact JSON body.
describe("sign-in and admin calls", () => {
  const USER: AdminUser = {
    id: "u2",
    email: "ravi@sharma.in",
    display_name: "Ravi",
    role: "member",
    disabled: false,
    created_at: "2026-09-01T10:00:00+00:00",
    locked_until: null,
    totp_enabled: false,
    two_step_turned_off: false,
    can_delete_forever: false,
    must_change_password: false,
    company_sign_in: "connected",
  };

  const calls: { url: string; init: RequestInit | undefined }[] = [];

  function reply(status: number, body?: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return new Response(body === undefined ? null : JSON.stringify(body), { status });
      }),
    );
  }

  function sent(index: number) {
    const call = calls[index];
    if (!call) throw new Error(`no call ${index}`);
    const body = call.init?.body;
    return {
      url: call.url,
      method: call.init?.method,
      headers: call.init?.headers,
      body: typeof body === "string" ? (JSON.parse(body) as unknown) : undefined,
    };
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    calls.length = 0;
  });

  // Changed on purpose (one sign-in page, 16 Oct 2026): /auth/admin/login and turning
  // 2FA on or off in Admin, Settings went; admins sign in and set up at /auth/login.



  it("allows or stops Delete forever", async () => {
    reply(200, USER);
    await setDeleteForever("u2", true);
    const url = "/api/admin/users/u2/delete-forever";
    expect(sent(0)).toMatchObject({ url, method: "PUT", body: { allowed: true } });
  });


  // Tushar, 30 Oct 2026: an admin turns their own two-step sign-in off, and on again.
  // Changed on purpose (#457 with #460): on the routes every role uses.




  it("signs out everywhere with an empty body", async () => {
    reply(204);
    await expect(logoutEverywhere()).resolves.toBeUndefined();
    expect(sent(0)).toMatchObject({ url: "/api/auth/logout-everywhere", method: "POST" });
    expect(sent(0).body).toBeUndefined();
  });

  it("unlocks and resets the authenticator of one user", async () => {
    reply(200, USER);
    expect(await unlockUser("u2")).toEqual(USER);
    expect(await resetUser2fa("u2")).toEqual(USER);
    expect(sent(0)).toMatchObject({ url: "/api/admin/users/u2/unlock", method: "POST" });
    expect(sent(1)).toMatchObject({ url: "/api/admin/users/u2/reset-2fa", method: "POST" });
  });
});

describe("upload limits and a session that ends", () => {
  const urls: string[] = [];

  function reply(status: number, body?: unknown) {
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return new Response(body === undefined ? null : JSON.stringify(body), { status });
    });
  }

  afterEach(() => {
    vi.unstubAllGlobals();
    onSessionEnded(null);
    urls.length = 0;
  });

  it("asks GET /upload-limits for the limits", async () => {
    const readers = [{ id: "auto", title: "Auto", hint: "The usual reader", available: true, reason: null }];
    const limits = { pdf_bytes: 1, image_bytes: 2, sheet_bytes: 3, max_pages: 4, readers };
    reply(200, limits);
    expect(await getUploadLimits()).toEqual(limits);
    expect(urls).toEqual(["/api/upload-limits"]);
  });

  it("tells the listener when a reply says the session has ended", async () => {
    const ended = vi.fn();
    onSessionEnded(ended);
    reply(401, { error: { code: "not_signed_in", message: "Please sign in." } });
    await expect(getUploadLimits()).rejects.toMatchObject({ status: 401, code: "not_signed_in" });
    expect(ended).toHaveBeenCalledTimes(1);
  });

  it("does not for any other refusal", async () => {
    const ended = vi.fn();
    onSessionEnded(ended);
    reply(401, { error: { code: "bad_credentials", message: "Wrong e-mail or password." } });
    await expect(getUploadLimits()).rejects.toBeInstanceOf(Error);
    reply(403, { error: { code: "admin_session_required", message: "Only admins can use this." } });
    await expect(getUploadLimits()).rejects.toMatchObject({ status: 403 });
    expect(ended).not.toHaveBeenCalled();
  });
});
