// Admin, People and Settings for connected accounts (#457 phase 1): who signs in how, a
// waiting connection's line, Remove connected accounts, and the auto-approval switch with
// its warning. The spec's copy, exactly.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ALREADY_DECIDED,
  AUTO_APPROVE_LABEL,
  AUTO_APPROVE_NOTE,
  AUTO_APPROVE_SAVED,
  CONNECTIONS_FAILED,
  autoApproveWarning,
  decideLabel,
  describeAction,
  pendingLine,
  removeConnectionsQuestion,
  signsInWith,
} from "@/components/admin/logic";
import type { PendingConnection } from "@/lib/types";

const source = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("signsInWith", () => {
  it("lists each way in, in plain words", () => {
    expect(signsInWith(["password", "google"])).toBe("Password, Google");
    expect(signsInWith(["password"])).toBe("Password");
    expect(signsInWith(undefined)).toBe("Password");
  });
});

const waiting: PendingConnection = {
  id: "i-1",
  user_id: "u-1",
  display_name: "Ramesh Kumar",
  user_email: "ramesh@firm.in",
  provider: "google",
  email: "ramesh.k@gmail.com",
  linked_at: "2026-10-30T06:00:00Z",
};

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
    // Changed on purpose (#457 review): the words name only the providers that are on.
    expect(autoApproveWarning(["google"])).toBe("Members' Google accounts will work at once, without your review.");
    expect(removeConnectionsQuestion("Ramesh", ["google"])).toBe(
      "Remove Ramesh's connected accounts? They can't sign in with Google any more. Their password still works.",
    );
  });

  it("say the switch saved at once, and that it does (#457 review)", () => {
    expect(AUTO_APPROVE_SAVED).toBe("Saved.");
    expect(AUTO_APPROVE_NOTE).toBe("This setting saves as soon as you change it.");
    const settings = source("components/admin/SettingsPanel.tsx");
    expect(settings).toContain("{AUTO_APPROVE_NOTE}");
    expect(settings).toContain("saved ? AUTO_APPROVE_SAVED : null");
  });

  it("hide the switch while the server leaves it out (no provider on)", () => {
    const settings = source("components/admin/SettingsPanel.tsx");
    expect(settings).toContain("if (settings.auto_approve_connections === undefined) return null;");
    expect(settings).not.toContain("pendingConnections");
  });

  it("label the audit line in plain words (#457 review)", () => {
    expect(describeAction("settings.auto_approve_on")).toBe("Turned on automatic approval of sign-in connections");
    expect(describeAction("settings.auto_approve_off")).toBe("Turned off automatic approval of sign-in connections");
  });

  it("are on the screens", () => {
    const users = source("components/admin/UsersPanel.tsx");
    expect(users).toContain("Connections to approve");
    expect(users).toContain("Signs in with");
    expect(users).toContain('"Remove connected accounts"');
    expect(users).toContain("approveConnection");
    expect(users).toContain("declineConnection");
    expect(users).toContain("removeConnectionsQuestion(name, providers)");
    const settings = source("components/admin/SettingsPanel.tsx");
    expect(settings).toContain("AUTO_APPROVE_LABEL");
    // Changed on purpose (#457 review): the warning is built from the providers that are on.
    expect(settings).toContain("autoApproveWarning(providers)");
  });
});

describe("Connections to approve (#457 review)", () => {
  const users = source("components/admin/UsersPanel.tsx");
  const from = users.slice(users.indexOf("function ConnectionsToApprove("));
  const section = from.slice(0, from.indexOf("\n}\n")); // the function alone

  it("labels Approve and Decline with the person and the provider, both secondary buttons", () => {
    expect(decideLabel(waiting, true)).toBe("Approve Ramesh Kumar's Google account");
    expect(decideLabel(waiting, false)).toBe("Decline Ramesh Kumar's Google account");
    expect(section).toContain("aria-label={decideLabel(row, true)}");
    expect(section).toContain("aria-label={decideLabel(row, false)}");
    expect(section).not.toContain("btn-primary");
  });

  it("hides only on a 404; any other failure says so, with Try again", () => {
    expect(CONNECTIONS_FAILED).toBe("Couldn't load connections to approve. Try again.");
    expect(section).toMatch(/setFailed\(!\(caught instanceof ApiError && caught\.status === 404\)\)/);
    expect(section).toContain("{CONNECTIONS_FAILED}");
    expect(section).toContain("setAttempt((n) => n + 1)");
  });

  it("reloads the list when another admin already decided", () => {
    expect(ALREADY_DECIDED).toBe("Another admin already decided this one.");
    expect(section).toMatch(/caught instanceof ApiError && caught\.status === 404[\s\S]*ALREADY_DECIDED[\s\S]*setAttempt/);
  });

  it("moves focus to the next row's Approve, else to the users heading", () => {
    expect(section).toContain("document.getElementById(next ? approveId(next.id) : USERS_HEADING_ID)?.focus();");
    expect(users).toMatch(/<h2 id=\{USERS_HEADING_ID\} tabIndex=\{-1\}/);
  });
});
