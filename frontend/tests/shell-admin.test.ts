import { describe, expect, it } from "vitest";
import {
  TABS,
  adminGate,
  allowForeverQuestion,
  describeAction,
  describeDetails,
  firstInvalidField,
  foreverControl,
  isLocked,
  lastRunText,
  lockedUntilText,
  pageRouteTiles,
  readerTiles,
  parseUploadLimit,
  retryErrorText,
  statTiles,
  tabAfterKey,
  userActions,
  userStatus,
  validateNewUser,
} from "@/components/admin/logic";
import { ApiError } from "@/lib/api";
import type { AdminUser, Me, Role, SessionScope, Stats } from "@/lib/types";

// 29 Sep 2026, 13:50 in India (08:20 UTC). A lock until 08:35 UTC is 14:05 there.
const NOW = Date.parse("2026-09-29T08:20:00Z");
const LOCK_END = "2026-09-29T08:35:00+00:00";

const user = (changes: Partial<AdminUser> = {}): AdminUser => ({
  id: "u2",
  email: "ravi@sharma.in",
  display_name: "Ravi",
  role: "member",
  disabled: false,
  created_at: "2026-09-01T10:00:00+00:00",
  locked_until: null,
  totp_enabled: false,
  must_change_password: false,
  can_delete_forever: false,
  ...changes,
});

describe("lockedUntilText and isLocked", () => {
  it("shows the lock's end in India time, on a 24-hour clock", () => {
    expect(lockedUntilText(LOCK_END)).toBe("Locked until 14:05");
    expect(lockedUntilText("2026-09-29T18:40:00Z")).toBe("Locked until 00:10");
  });

  it("is locked only until the lock's end", () => {
    expect(isLocked(user({ locked_until: LOCK_END }), NOW)).toBe(true);
    expect(isLocked(user({ locked_until: LOCK_END }), Date.parse(LOCK_END))).toBe(false);
    expect(isLocked(user({ locked_until: "2026-09-29T08:00:00Z" }), NOW)).toBe(false);
    expect(isLocked(user(), NOW)).toBe(false);
    expect(isLocked(user({ locked_until: "not a date" }), NOW)).toBe(false);
  });
});

describe("userStatus", () => {
  const texts = (u: AdminUser, now = NOW) => userStatus(u, now).map((part) => part.text);

  it("says Active for a normal account", () => {
    expect(userStatus(user(), NOW)).toEqual([{ text: "Active", tone: "ready" }]);
  });

  it("says Locked until 14:05 while locked", () => {
    expect(userStatus(user({ locked_until: LOCK_END }), NOW)).toEqual([
      { text: "Locked until 14:05", tone: "danger" },
    ]);
  });

  it("goes back to Active once the lock has passed, even before a reload", () => {
    expect(texts(user({ locked_until: LOCK_END }), Date.parse("2026-09-29T08:36:00Z"))).toEqual([
      "Active",
    ]);
  });

  it("says Disabled for a disabled account, lock or not", () => {
    expect(userStatus(user({ disabled: true, locked_until: LOCK_END }), NOW)).toEqual([
      { text: "Disabled", tone: "danger" },
    ]);
  });

  it("says 2FA on or off for admins", () => {
    expect(texts(user({ role: "admin", totp_enabled: true }))).toEqual(["Active", "2FA on"]);
    expect(texts(user({ role: "admin" }))).toEqual(["Active", "2FA off"]);
    expect(texts(user({ role: "admin", disabled: true }))).toEqual(["Disabled", "2FA off"]);
  });

  it("says nothing about 2FA for other roles unless it is on", () => {
    expect(texts(user({ role: "reviewer" }))).toEqual(["Active"]);
    expect(texts(user({ role: "member", totp_enabled: true }))).toEqual(["Active", "2FA on"]);
  });

  it("marks an account that still has a temporary password", () => {
    expect(texts(user({ must_change_password: true }))).toEqual(["Active", "Temporary password"]);
  });
});

describe("userActions", () => {
  const actions = (u: AdminUser, isSelf = false) => userActions(u, { isSelf, now: NOW });

  it("offers disable and reset password for another user", () => {
    expect(actions(user())).toEqual({
      toggleDisabled: true,
      unlock: false,
      resetPassword: true,
      reset2fa: false,
    });
  });

  it("offers Unlock only while locked", () => {
    expect(actions(user({ locked_until: LOCK_END })).unlock).toBe(true);
    expect(actions(user({ locked_until: "2026-09-29T08:00:00Z" })).unlock).toBe(false);
  });

  it("offers Reset 2FA only when 2FA is on", () => {
    expect(actions(user({ role: "admin", totp_enabled: true })).reset2fa).toBe(true);
    expect(actions(user({ role: "admin" })).reset2fa).toBe(false);
  });

  it("offers an admin nothing on their own row (the server refuses it too)", () => {
    expect(actions(user({ role: "admin", totp_enabled: true }), true)).toEqual({
      toggleDisabled: false,
      unlock: false,
      resetPassword: false,
      reset2fa: false,
    });
  });
});

describe("Delete forever, granted (council 4 Oct 2026)", () => {
  const control = (u: AdminUser, isSelf = false, adminTwoStep = true) =>
    foreverControl(u, { isSelf, adminTwoStep });

  it("offers Allow, or Stop once allowed, for a member or reviewer; nothing for an admin or yourself", () => {
    expect(control(user())).toEqual({ kind: "allow", disabledReason: null });
    expect(control(user({ role: "reviewer", can_delete_forever: true }), false, false).kind).toBe("stop");
    expect([control(user({ role: "admin" })).kind, control(user(), true).kind]).toEqual(["none", "none"]);
    expect(control(user(), false, false)).toEqual({
      kind: "allow",
      disabledReason: "Turn on your two-step sign-in in Settings to allow this.",
    });
  });

  it("asks first, says it covers all of Trash, and shows the chip and the audit words", () => {
    expect(allowForeverQuestion("Ravi")).toBe(
      "Let Ravi delete documents in Trash forever? This covers every document in Trash, " +
        "not only theirs, and can't be undone. They will type their password each time.",
    );
    expect(userStatus(user({ can_delete_forever: true }), NOW).map((part) => part.text)).toEqual([
      "Active",
      "Can delete forever",
    ]);
    expect(describeAction("user.delete_forever_on")).toBe("Allowed a user to delete forever");
    expect(describeAction("user.delete_forever_off")).toBe("Stopped a user deleting forever");
  });
});

describe("adminGate", () => {
  const me = (role: Role, scope: SessionScope): Me => ({
    user: {
      id: "u1",
      email: "asha@sharma.in",
      display_name: "Asha",
      role,
      must_change_password: false,
      totp_enabled: false,
      can_delete_forever: false,
    },
    customer: { id: "c1", name: "Sharma Traders" },
    session: { scope },
  });

  it("opens the admin screen only for an admin session", () => {
    expect(adminGate(me("admin", "admin"))).toBe("allow");
  });

  it("sends an admin with an app session to admin sign-in", () => {
    expect(adminGate(me("admin", "app"))).toBe("admin_sign_in");
  });

  it("sends anyone else to admin sign-in too", () => {
    expect(adminGate(me("member", "app"))).toBe("admin_sign_in");
    expect(adminGate(me("reviewer", "admin"))).toBe("admin_sign_in");
  });
});

describe("tabAfterKey", () => {
  const count = TABS.length;

  it("moves with the arrow keys and wraps round", () => {
    expect(tabAfterKey(0, "ArrowRight", count)).toBe(1);
    expect(tabAfterKey(count - 1, "ArrowRight", count)).toBe(0);
    expect(tabAfterKey(0, "ArrowLeft", count)).toBe(count - 1);
  });

  it("jumps with Home and End, and ignores other keys", () => {
    expect(tabAfterKey(2, "Home", count)).toBe(0);
    expect(tabAfterKey(0, "End", count)).toBe(count - 1);
    expect(tabAfterKey(1, "Enter", count)).toBeNull();
  });
});

describe("validateNewUser", () => {
  const good = {
    email: "ravi@sharma.in",
    display_name: "Ravi",
    role: "member" as const,
    password: "twelve chars",
  };

  it("accepts a complete user", () => {
    expect(validateNewUser(good)).toEqual({});
  });

  it("names each problem against its field", () => {
    expect(validateNewUser({ ...good, email: "ravi", display_name: "  ", password: "short" })).toEqual({
      email: "Enter a valid e-mail address.",
      display_name: "Enter a name.",
      password: "Use at least 12 characters.",
    });
  });

  it("needs a password of at least 12 characters", () => {
    expect(validateNewUser({ ...good, password: "elevenchars" }).password).toBeDefined();
    expect(validateNewUser({ ...good, password: "twelvechars!" }).password).toBeUndefined();
  });
});

describe("firstInvalidField", () => {
  it("picks the first problem in screen order, for focus", () => {
    expect(firstInvalidField({ password: "x", email: "y" })).toBe("email");
    expect(firstInvalidField({ password: "x", display_name: "y" })).toBe("display_name");
    expect(firstInvalidField({ password: "x" })).toBe("password");
    expect(firstInvalidField({})).toBeNull();
  });
});

describe("parseUploadLimit", () => {
  it("treats an empty box as the platform limit", () => {
    expect(parseUploadLimit("  ", 50)).toEqual({ ok: true, value: null });
  });

  it("accepts whole numbers from 1 to the platform limit", () => {
    expect(parseUploadLimit("1", 50)).toEqual({ ok: true, value: 1 });
    expect(parseUploadLimit(" 50 ", 50)).toEqual({ ok: true, value: 50 });
  });

  it("refuses anything else", () => {
    expect(parseUploadLimit("0", 50)).toEqual({
      ok: false,
      message: "The limit must be at least 1 MB.",
    });
    expect(parseUploadLimit("51", 50)).toEqual({
      ok: false,
      message: "The limit can be at most 50 MB.",
    });
    expect(parseUploadLimit("2.5", 50).ok).toBe(false);
    expect(parseUploadLimit("-3", 50).ok).toBe(false);
    expect(parseUploadLimit("ten", 50).ok).toBe(false);
  });
});

describe("statTiles", () => {
  const stats: Stats = {
    documents_by_status: { ready: 120000, failed: 2 },
    questions_total: 40,
    answered: 30,
    not_found: 10,
    verified_answers: 25,
    needs_review_answers: 5,
    feedback_up: 7,
    feedback_down: 1,
    open_review_items: 3,
    older_versions: 12,
    in_trash: 4,
    pages_this_month: { cells: 0, text_layer: 1200, ocr: 3, blank: 0 },
  };

  it("adds up documents and fills missing statuses with zero", () => {
    const tiles = statTiles(stats);
    expect(tiles[0]).toEqual({ label: "Documents", value: "1,20,002" });
    expect(tiles.find((tile) => tile.label === "Documents being read")?.value).toBe("0");
  });

  it("shows the older versions kept and what is in Trash apart from the documents", () => {
    const tiles = statTiles(stats);
    expect(tiles.find((tile) => tile.label === "Other versions kept")?.value).toBe("12");
    expect(tiles.find((tile) => tile.label === "In Trash")?.value).toBe("4");
    expect(tiles[0]?.value).toBe("1,20,002");
  });

  it("says Verified and Needs review in words", () => {
    const labels = statTiles(stats).map((tile) => tile.label);
    expect(labels).toContain("Answers labelled Verified");
    expect(labels).toContain("Answers labelled Needs review");
  });
});

describe("pageRouteTiles (PRD 2, R6)", () => {
  it("shows the pages read this month by route, in a fixed order, zeros included", () => {
    const pages_this_month = { ocr: 3, blank: 0, text_layer: 120000, cells: 0 };
    expect(pageRouteTiles({ pages_this_month })).toEqual([
      { label: "Spreadsheet cells", value: "0" },
      { label: "PDF text", value: "1,20,000" },
      { label: "OCR", value: "3" },
      { label: "Blank pages", value: "0" },
    ]);
  });
});

describe("describeDetails", () => {
  it("lists keys in order as plain text", () => {
    expect(describeDetails({ status: "ready", count: 3, ok: true, parent: null })).toBe(
      "count: 3, ok: true, parent: none, status: ready",
    );
  });

  it("shows nested values as JSON text, never markup", () => {
    expect(describeDetails({ ids: ["a", "b"] })).toBe('ids: ["a","b"]');
    expect(describeDetails({ status: "<b>x</b>" })).toBe("status: <b>x</b>");
  });

  it("is empty for no details", () => {
    expect(describeDetails({})).toBe("");
  });
});

describe("describeAction", () => {
  it("puts known actions in words and leaves unknown ones as they are", () => {
    expect(describeAction("document.upload")).toBe("Uploaded a document");
    expect(describeAction("something.new")).toBe("something.new");
  });

  it("puts the sign-in and account actions of PRD A7 in words", () => {
    const actions = [
      "auth.admin_login",
      "auth.login_failed",
      "auth.locked",
      "auth.enrol",
      "auth.password_change",
      "auth.logout_everywhere",
      "user.unlock",
      "user.reset_password",
      "user.reset_totp",
    ];
    for (const action of actions) {
      expect(describeAction(action), action).not.toBe(action);
    }
    expect(describeAction("user.reset_totp")).toBe("Reset a user's 2FA");
  });
});

describe("the failed jobs tab", () => {
  it("is the last tab", () => {
    expect(TABS.at(-1)).toEqual({ id: "failed", label: "Failed jobs" });
  });

  it("says when a document was last tried, or that it never ran", () => {
    expect(lastRunText(null)).toBe("Never run");
    expect(lastRunText("2026-10-01T09:00:00+00:00")).toMatch(/^Last tried .*2026/);
  });

  it("shows the server's words for a refused Retry, and a hint when it can't be reached", () => {
    const stale = new ApiError(409, "not_reprocessable", "This document can't be read again now.");
    const busy = new ApiError(429, "rate_limited", "Please wait and try again.");
    expect(retryErrorText(stale)).toBe("This document can't be read again now.");
    expect(retryErrorText(busy)).toBe("Please wait and try again.");
    expect(retryErrorText(new TypeError("fetch failed"))).toBe(
      "Could not reach the server. Check your connection, then try again.",
    );
  });
});

describe("readerTiles (PRD 5 U9)", () => {
  const reader_pages = { gemini: 1200, local: 30, cached: 4, read_again: 2 };

  it("shows the scanned pages by who read them, then the spend, labelled an estimate", () => {
    const spend_estimate = { input_tokens: 900000, output_tokens: 120000, usd: 1.5 };
    expect(readerTiles({ reader_pages, spend_estimate })).toEqual([
      { label: "Read by Gemini", value: "1,200" },
      { label: "Read by the backup reader", value: "30" },
      { label: "From the cache", value: "4" },
      { label: "Read again", value: "2" },
      { label: "Gemini spend this month (estimate)", value: "$1.50" },
    ]);
  });

  it("hides the money while the prices are not known, and everything from an older server", () => {
    const unknown = { input_tokens: 900000, output_tokens: 120000, usd: null };
    expect(readerTiles({ reader_pages, spend_estimate: unknown }).map((tile) => tile.label)).not.toContain(
      "Gemini spend this month (estimate)",
    );
    expect(readerTiles({})).toEqual([]);
  });
});
