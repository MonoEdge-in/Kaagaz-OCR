// Admin, People and Settings for connected accounts (#457 phase 1): who signs in how, a
// waiting connection's line, Remove connected accounts, and the auto-approval switch with
// its warning. The spec's copy, exactly.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  AUTO_APPROVE_LABEL,
  AUTO_APPROVE_WARNING,
  REMOVE_CONNECTIONS_CONFIRM,
  pendingLine,
  signsInWith,
} from "@/components/admin/logic";

const source = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("signsInWith", () => {
  it("lists each way in, in plain words", () => {
    expect(signsInWith(["password", "google"])).toBe("Password, Google");
    expect(signsInWith(["password"])).toBe("Password");
    expect(signsInWith(undefined)).toBe("Password");
  });
});

describe("pendingLine", () => {
  it("says who, the provider, the email and when", () => {
    expect(
      pendingLine({
        id: "i-1",
        user_id: "u-1",
        display_name: "Ramesh Kumar",
        user_email: "ramesh@firm.in",
        provider: "google",
        email: "ramesh.k@gmail.com",
        linked_at: "2026-10-30T06:00:00Z",
      }),
    ).toBe("Ramesh Kumar · Google · ramesh.k@gmail.com · asked 30 Oct");
  });
});

describe("the words", () => {
  it("are the spec's", () => {
    expect(AUTO_APPROVE_LABEL).toBe("Approve new sign-in connections automatically");
    expect(AUTO_APPROVE_WARNING).toBe("Members' Google or Microsoft accounts will work at once, without your review.");
    expect(REMOVE_CONNECTIONS_CONFIRM).toBe(
      "Stops this person signing in with Google or Microsoft. Their password still works.",
    );
  });

  it("are on the screens", () => {
    const users = source("components/admin/UsersPanel.tsx");
    expect(users).toContain("Connections to approve");
    expect(users).toContain("Signs in with");
    expect(users).toContain('"Remove connected accounts"');
    expect(users).toContain("approveConnection");
    expect(users).toContain("declineConnection");
    const settings = source("components/admin/SettingsPanel.tsx");
    expect(settings).toContain("AUTO_APPROVE_LABEL");
    expect(settings).toContain("AUTO_APPROVE_WARNING");
  });
});
