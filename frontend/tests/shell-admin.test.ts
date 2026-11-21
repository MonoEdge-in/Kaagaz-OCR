import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  TABS,
  TO_VERIFY_TIP,
  adminGate,
  allowForeverQuestion,
  breakdowns,
  kpiTiles,
  settingsChanged,
  twoStepNote,
  describeAction,
  describeDetails,
  describeTarget,
  firstInvalidField,
  foreverControl,
  isLocked,
  lastRunText,
  lockedUntilText,
  parseUploadLimit,
  retryErrorText,
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
  two_step_turned_off: false,
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

// Changed on purpose (#457 phase 3): anyone may have codes, so every row says Two-step: On or Off.
describe("userStatus", () => {
  const texts = (u: AdminUser, now = NOW) => userStatus(u, now).map((part) => part.text);

  it("says Active for a normal account", () => {
    expect(userStatus(user(), NOW)).toEqual([{ text: "Active", tone: "ready" }, { text: "Two-step: Off", tone: "neutral" }]);
  });

  it("says Locked until 14:05 while locked", () => {
    expect(userStatus(user({ locked_until: LOCK_END }), NOW)).toEqual([
      { text: "Locked until 14:05", tone: "danger" },
      { text: "Two-step: Off", tone: "neutral" },
    ]);
  });

  it("goes back to Active once the lock has passed, even before a reload", () => {
    expect(texts(user({ locked_until: LOCK_END }), Date.parse("2026-09-29T08:36:00Z"))).toEqual([
      "Active",
      "Two-step: Off",
    ]);
  });

  it("says Disabled for a disabled account, lock or not", () => {
    expect(userStatus(user({ disabled: true, locked_until: LOCK_END }), NOW)).toEqual([
      { text: "Disabled", tone: "danger" },
      { text: "Two-step: Off", tone: "neutral" },
    ]);
  });

  // Changed on purpose (plain words, 25 Oct 2026): "Two-step", as the rest of the product, not "2FA".
  it("says two-step on or off for admins", () => {
    expect(texts(user({ role: "admin", totp_enabled: true }))).toEqual(["Active", "Two-step: On"]);
    expect(texts(user({ role: "admin" }))).toEqual(["Active", "Two-step: Off"]);
    expect(texts(user({ role: "admin", disabled: true }))).toEqual(["Disabled", "Two-step: Off"]);
  });

  it("says two-step on or off for other roles too", () => {
    expect(texts(user({ role: "reviewer" }))).toEqual(["Active", "Two-step: Off"]);
    expect(texts(user({ role: "member", totp_enabled: true }))).toEqual(["Active", "Two-step: On"]);
  });

  it("marks an account that still has a temporary password", () => {
    expect(texts(user({ must_change_password: true }))).toEqual(["Active", "Two-step: Off", "Temporary password"]);
  });
});

describe("userActions", () => {
  const actions = (u: AdminUser, isSelf = false) => userActions(u, { isSelf, now: NOW });

  it("offers disable and reset password for another user", () => {
    expect(actions(user())).toEqual({
      toggleDisabled: true,
      unlock: false,
      resetPassword: true,
      reset2fa: true,
    });
  });

  it("offers Unlock only while locked", () => {
    expect(actions(user({ locked_until: LOCK_END })).unlock).toBe(true);
    expect(actions(user({ locked_until: "2026-09-29T08:00:00Z" })).unlock).toBe(false);
  });

  it("offers Reset authenticator to members and reviewers, not to an admin (#737)", () => {
    expect(actions(user({ role: "member" })).reset2fa).toBe(true);
    expect(actions(user({ role: "reviewer", totp_enabled: true })).reset2fa).toBe(true);
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
      disabledReason: "Turn on two-step sign-in in Account to allow this.",
    });
  });

  it("asks first, says it covers all of Trash, and shows the chip and the audit words", () => {
    expect(allowForeverQuestion("Ravi")).toBe(
      "Let Ravi delete documents in Trash for good? This covers every document in Trash, " +
        "not only theirs, and can't be undone. They will type their password each time.",
    );
    expect(userStatus(user({ can_delete_forever: true }), NOW).map((part) => part.text)).toEqual([
      "Active",
      "Two-step: Off",
      "Can delete for good",
    ]);
    expect(describeAction("user.delete_forever_on")).toBe("Allowed a user to delete for good");
    expect(describeAction("user.delete_forever_off")).toBe("Stopped a user deleting for good");
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
      two_step_turned_off: false,
      can_delete_forever: false,
      email_confirmed: true, // #457
    },
    customer: { id: "c1", name: "Sharma Traders" },
    session: { scope },
    providers: [], // #457 review: the sign-in providers switched on
  });

  it("opens the admin screen only for an admin session", () => {
    expect(adminGate(me("admin", "admin"))).toBe("allow");
  });

  it("sends an admin with an app session to admin sign-in", () => {
    expect(adminGate(me("admin", "app"))).toBe("admin_sign_in");
  });

  // N4 (QA 17 Oct): a member was sent to sign in, which could never help; it says Admin is for admins.
  it("tells anyone else it is for admins, never sends them to sign in", () => {
    expect(adminGate(me("member", "app"))).toBe("no_access");
    expect(adminGate(me("reviewer", "admin"))).toBe("no_access");
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
  };

  it("accepts a complete user: there is no password to type (#737)", () => {
    expect(validateNewUser(good)).toEqual({});
  });

  it("names each problem against its field", () => {
    expect(validateNewUser({ ...good, email: "ravi", display_name: "  " })).toEqual({
      email: "Enter a valid e-mail address.",
      display_name: "Enter a name.",
    });
  });
});

describe("firstInvalidField", () => {
  it("picks the first problem in screen order, for focus", () => {
    expect(firstInvalidField({ display_name: "x", email: "y" })).toBe("email");
    expect(firstInvalidField({ display_name: "y" })).toBe("display_name");
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

// Changed on purpose (UI audit, 16 Oct 2026): AD4, four large numbers, then small lists with
// plain labels; a row that is 0 is left out, a list with nothing says so.
describe("kpiTiles and breakdowns", () => {
  const stats: Stats = {
    documents_by_status: { ready: 120000, failed: 2 },
    questions_total: 40,
    answered: 30,
    not_found: 10,
    verified_answers: 25,
    needs_review_answers: 5,
    feedback_up: 7,
    feedback_down: 2,
    open_review_items: 3,
    older_versions: 12,
    in_trash: 4,
    pages_this_month: { cells: 0, text_layer: 1200, ocr: 3, blank: 0 },
  };

  // Changed on purpose (Tushar, 16 Oct 2026: Ready to verify), with a tooltip on its label.
  it("shows Total documents, Ready, Ready to verify and Failed, with Indian digit grouping", () => {
    expect(kpiTiles(stats)).toEqual([
      { label: "Total documents", value: "1,20,002" },
      { label: "Ready", value: "1,20,000" },
      { label: "Ready to verify", value: "3", note: "Values to verify in Review", tip: TO_VERIFY_TIP },
      { label: "Failed", value: "2" },
    ]);
  });

  it("leaves the zeros out of the small lists, in a fixed order", () => {
    const [pipeline, questions, pages, kept] = breakdowns(stats);
    // Changed on purpose (plain words, 25 Oct 2026): it was "Pipeline".
    expect(pipeline).toEqual({ title: "Not ready yet", none: "Nothing is waiting or being read.", rows: [] });
    expect(questions?.rows.map((row) => row.label)).toEqual([
      "Questions asked",
      "Answered",
      "Not found in the documents",
      "Answers labelled Verified",
      "Answers labelled Ready to verify", // Changed on purpose (Tushar, 16 Oct 2026: Ready to verify)
      "Useful", // the product's words (PRD 6 F1), never "Thumbs up"
      "Not useful",
    ]);
    // Plain words, no "OCR" (UI quality council, 15 Oct 2026).
    expect(pages?.rows).toEqual([
      { label: "From PDF text", value: "1,200" },
      { label: "Scanned pages", value: "3" },
    ]);
    expect(kept?.rows).toEqual([
      { label: "Other versions kept", value: "12" },
      { label: "In Trash", value: "4" },
    ]);
  });
});


describe("settingsChanged (AD6) and twoStepNote (AD7)", () => {
  const saved = { max_upload_mb: 25, autofile: { granted: true, on: false } };

  it("is true only when the form differs from what is saved", () => {
    expect(settingsChanged(saved, "25", false)).toBe(false);
    expect(settingsChanged(saved, " 25 ", false)).toBe(false);
    expect(settingsChanged(saved, "30", false)).toBe(true);
    expect(settingsChanged(saved, "25", true)).toBe(true);
    expect(settingsChanged({ max_upload_mb: null, autofile: { granted: false, on: false } }, "", true)).toBe(false);
  });

  it("says plainly where two-step sign-in stands", () => {
    // Changed on purpose (MFA required, 16 Oct 2026): two-step is required for admins.
    expect(twoStepNote()).toBe("Admins without two-step sign-in set it up at their next sign-in.");
  });
});

// Changed on purpose (UI quality council, 15 Oct 2026): only known keys, in words; ids,
// the model a Read again asked for and any other key are left out.
describe("describeDetails", () => {
  it("says the known keys in words, in key order", () => {
    expect(describeDetails({ status: "ready", page_no: 3 })).toBe("Page 3, Status: Ready");
    expect(describeDetails({ versions: 2 })).toBe("2 versions");
    expect(describeDetails({ versions: 1, tag_count: 1 })).toBe("1 tag, 1 version");
    expect(describeDetails({ tag_count: 3 })).toBe("3 tags");
    expect(describeDetails({ max_upload_mb: 25 })).toBe("Upload limit 25 MB");
    expect(describeDetails({ max_upload_mb: null })).toBe("Upload limit: the platform's");
    expect(describeDetails({ role: "reviewer", disabled: false, password_changed: true })).toBe("Role: Reviewer");
  });

  it("leaves out ids, the model, unknown keys and values it has no words for", () => {
    expect(
      describeDetails({
        status: "failed",
        read_model: "google/gemini-3.1-pro-preview",
        folder_id: "8f0c",
        document_id: "d1",
        conversation_id: "c1",
      }),
    ).toBe("Status: Failed");
    expect(describeDetails({ status: "answered", reason: "bad_password", ids: ["a"] })).toBe("");
    expect(describeDetails({ role: "<b>x</b>" })).toBe("");
  });

  it("is empty for no details", () => {
    expect(describeDetails({})).toBe("");
  });
});

describe("describeTarget", () => {
  it("names what a row was done on in a word, never its id", () => {
    expect(describeTarget("document")).toBe("Document");
    expect(describeTarget("user")).toBe("User");
    expect(describeTarget("folder")).toBe("Folder");
    expect(describeTarget("tag")).toBe("Tag");
    expect(describeTarget("customer")).toBe("Settings");
    expect(describeTarget(null)).toBe("—");
    expect(describeTarget("something_new")).toBe("—");
  });

  // Changed on purpose (PRD 1, screens): File management A5, the Document column names the
  // document, and what anything else was done on when the row is not about one.
  // Changed on purpose (issue 375, 27 Oct 2026): through activityTarget, which never says
  // "User" for an account event (tests/activity-subject.test.ts).
  it("is what the Activity tab shows, with no target id", () => {
    const panel = readFileSync(join(__dirname, "..", "components/admin/ActivityPanel.tsx"), "utf8");
    expect(panel).toContain("{activityTarget(entry)}");
    expect(panel).not.toContain("target_id");
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
    // Changed on purpose (plain words, 25 Oct 2026): "two-step sign-in", not "2FA".
    expect(describeAction("user.reset_totp")).toBe("Reset a user's two-step sign-in");
    // #457: resets are audited as auth.totp_reset since 0063; both names read the same.
    expect(describeAction("auth.totp_reset")).toBe("Reset a user's two-step sign-in");
  });

  it("has words for every action the backend writes to the audit log", () => {
    // Every "family.action" string literal in backend/kaagaz of the families audit_log holds.
    const root = join(__dirname, "..", "..", "backend", "kaagaz");
    const files = readdirSync(root, { recursive: true, encoding: "utf8" }).filter((file) => file.endsWith(".py"));
    const family = "auth|user|user_word|document|document_story|folder|tag|review|chat|settings|customer";
    const pattern = new RegExp(`"((?:${family})\\.[a-z_]+)"`, "g");
    const actions = new Set<string>();
    for (const file of files) {
      for (const [, action = ""] of readFileSync(join(root, file), "utf8").matchAll(pattern)) actions.add(action);
    }
    expect(actions.size).toBeGreaterThan(30);
    for (const action of actions) {
      expect(describeAction(action), action).not.toBe(action);
    }
  });
});

// UI mockups board 7 (16 Oct 2026): the organisation's name under the Admin heading.
describe("the admin header", () => {
  // Changed on purpose (UI audit, 16 Oct 2026): G12, the name with its label, never alone as a title.
  it("names the organisation under the heading, with a label", () => {
    const view = readFileSync(join(__dirname, "..", "components", "admin", "AdminView.tsx"), "utf8");
    expect(view).toMatch(/<h1>Admin<\/h1>[\s\S]{0,200}<p>\s*Workspace: <strong>\{me\.customer\.name\}<\/strong>\s*<\/p>/);
  });
});

// Changed on purpose (UI quality council, 15 Oct 2026): "Failed documents", and no AI
// models tab (the models are the operator's only).
describe("the failed documents tab", () => {
  it("is the last tab", () => {
    expect(TABS.at(-1)).toEqual({ id: "failed", label: "Failed documents" });
  });

  it("comes after the Usage tab, with no AI models tab", () => {
    expect(TABS.map((tab) => tab.label).slice(-2)).toEqual(["Usage", "Failed documents"]);
    expect(TABS.map((tab) => tab.id)).not.toContain("models");
  });

  it("says when a document was last tried, or that it was not tried yet", () => {
    expect(lastRunText(null)).toBe("Not tried yet");
    expect(lastRunText("2026-10-01T09:00:00+00:00")).toMatch(/^Last tried .*2026/);
  });

  it("shows the server's words for a refused Retry, and a hint when it can't be reached", () => {
    const stale = new ApiError(409, "not_reprocessable", "This document can't be read again now.");
    const busy = new ApiError(429, "rate_limited", "Please wait and try again.");
    expect(retryErrorText(stale)).toBe("This document can't be read again now.");
    expect(retryErrorText(busy)).toBe("Please wait and try again.");
    // Changed on purpose (#468 review, PT2/UX9): the one network wording (NETWORK_FAILURE).
    expect(retryErrorText(new TypeError("fetch failed"))).toBe(
      "Could not reach Kaagaz. Check your internet connection and try again.",
    );
  });
});
