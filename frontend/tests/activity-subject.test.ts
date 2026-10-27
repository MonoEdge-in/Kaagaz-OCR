// Issue 375 (27 Oct 2026): the admin Activity tab's Document column never says "User" for
// an account event, and a person added by Kaagaz's set-up command is not "Someone".

import { describe, expect, it } from "vitest";
import { activityTarget, activityWho } from "@/components/activity/words";
import type { ActivityEntry } from "@/lib/types";

const PRIYA = "5d6e7f80-9a1b-4c2d-8e3f-4a5b6c7d8e9f";
const ANITA = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d";

function entry(overrides: Partial<ActivityEntry>): ActivityEntry {
  return {
    id: 1,
    action: "auth.login",
    actor_id: PRIYA,
    actor_name: "Priya",
    target_type: "user",
    target_id: PRIYA,
    subject_name: "Priya",
    document: null,
    folder: null,
    details: {},
    created_at: "2026-10-25T10:00:00Z",
    ...overrides,
  };
}

describe("the Document column of an account event", () => {
  it("is empty when the person did it to their own account", () => {
    for (const action of ["auth.login", "auth.logout", "auth.totp_on", "auth.password_change"]) {
      expect(activityTarget(entry({ action }))).toBe("—");
    }
  });

  it("names the person it was about when someone else did it", () => {
    expect(activityTarget(entry({ action: "user.create", actor_id: ANITA, actor_name: "Anita" }))).toBe("Priya");
    expect(activityTarget(entry({ action: "auth.login_failed", actor_id: null, actor_name: null }))).toBe("Priya");
  });

  it("is empty for a person since removed, and never says User", () => {
    const gone = entry({ action: "user.update", actor_id: ANITA, actor_name: "Anita", subject_name: null });
    expect(activityTarget(gone)).toBe("—");
    expect(activityTarget(entry({}))).not.toBe("User");
  });

  it("still names a document, and what anything else was done on", () => {
    const document = { id: PRIYA, name: "patil-bill.pdf", version_no: 1, deleted: false };
    expect(activityTarget(entry({ action: "document.view", target_type: "document", document }))).toBe("patil-bill.pdf");
    expect(activityTarget(entry({ action: "folder.create", target_type: "folder", subject_name: undefined }))).toBe("Folder");
  });
});

describe("who did an account event", () => {
  it("is Kaagaz for a user its set-up command added, not Someone", () => {
    const added = entry({ action: "user.create", actor_id: null, actor_name: null });
    expect(activityWho(added)).toBe("Kaagaz");
    expect(activityWho({ ...added, action: "user.reset_totp" })).toBe("Kaagaz");
  });

  it("stays Someone for a failed sign-in, and a dash rule for a removed admin", () => {
    expect(activityWho(entry({ action: "auth.login_failed", actor_id: null, actor_name: null }))).toBe("Someone");
    // An admin since removed still has an id: their row is not Kaagaz's.
    expect(activityWho(entry({ action: "user.create", actor_id: ANITA, actor_name: null }))).toBe("Someone");
  });
});
