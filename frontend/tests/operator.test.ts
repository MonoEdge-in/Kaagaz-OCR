// The operator (super admin) screens, PRs 6 and 7: the pure rules, and source
// guards for what a render test would check (labels, the temporary password
// never stored, the operator area kept apart from the customer app).

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match";
import { describe, expect, it } from "vitest";
import { CODE_INVALID, CODE_MISSING } from "@/app/admin/login/adminSignIn";
import { EMAIL_INVALID, NETWORK_FAILURE, PASSWORD_MISSING, SIGN_IN_DOWN, TOO_MANY_ATTEMPTS } from "@/app/login/signIn";
import {
  CHECK_INPUT,
  CONFLICT,
  DISABLE_EFFECT,
  KEY_HELP,
  NAME_MISMATCH,
  OPERATOR_CHANNEL,
  OPERATOR_DOWN,
  OPERATOR_SIGN_IN_FAILED,
  ORG_NOT_FOUND,
  REFUSALS,
  SIGN_OUT_FAILED,
  STEP_UP_FAILED,
  TEMPORARY_PASSWORD_NOTE,
  afterDone,
  canCloseDialog,
  deleteCountdown,
  deleteEffect,
  exitUrl,
  expiryDelay,
  filterOrgs,
  firstInvalidOperator,
  firstInvalidOrgField,
  lastActivityText,
  meFailed,
  nameError,
  operatorErrorMessage,
  operatorSignInError,
  orgStatus,
  recheckAfter,
  searchSummary,
  signOut,
  userCounts,
  validateNewOrg,
  validateOperatorSignIn,
} from "@/components/operator/logic";
import { ApiError } from "@/lib/api";
import { SESSION_CHANNEL } from "@/lib/sessionChannel";
import nextConfig from "@/next.config";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? files(path) : [path];
  });
}

const OPERATOR_FILES = [
  ...files(join(ROOT, "app", "operator")),
  ...files(join(ROOT, "components", "operator")),
  join(ROOT, "lib", "operatorSession.tsx"),
].map((path) => relative(ROOT, path).replaceAll("\\", "/"));

const NOW = Date.parse("2026-10-06T08:20:00Z");

describe("operator sign-in", () => {
  it("needs the e-mail, the password and a 6-digit code", () => {
    const none = { email: "Enter your e-mail address.", password: PASSWORD_MISSING, code: CODE_MISSING };
    expect(validateOperatorSignIn("", "", "")).toEqual(none);
    expect(validateOperatorSignIn("op@monoedge.in", "pw", "123 456")).toEqual({ email: null, password: null, code: null });
    expect(validateOperatorSignIn("op", "pw", "12345").email).toBe(EMAIL_INVALID);
    expect(validateOperatorSignIn("op@monoedge.in", "pw", "१२३४५६").code).toBe(CODE_INVALID);
  });

  it("focuses the first field to fix, in form order", () => {
    expect(firstInvalidOperator({ email: null, password: "x", code: "y" })).toBe("password");
    expect(firstInvalidOperator({ email: null, password: null, code: "y" })).toBe("code");
    expect(firstInvalidOperator({ email: null, password: null, code: null })).toBeNull();
  });

  it("says the same thing for every 401, whatever was wrong", () => {
    expect(OPERATOR_SIGN_IN_FAILED.startsWith("Those details are not right.")).toBe(true);
    const said = "Email or password is wrong, or the account is locked. Try again in 15 minutes.";
    expect(operatorSignInError(new ApiError(401, "bad_credentials", said))).toBe(OPERATOR_SIGN_IN_FAILED);
    expect(operatorSignInError(new ApiError(400, "bad_request", "x"))).toBe(OPERATOR_SIGN_IN_FAILED);
    expect(operatorSignInError(new ApiError(429, "rate_limited", "x"))).toBe(TOO_MANY_ATTEMPTS);
    expect(operatorSignInError(new ApiError(0, "error", "x"))).toBe(NETWORK_FAILURE);
    expect(operatorSignInError(new TypeError("fetch failed"))).toBe(NETWORK_FAILURE);
    expect(operatorSignInError(new ApiError(503, "backend_unavailable", "x"))).toBe(SIGN_IN_DOWN);
  });
});

describe("operatorErrorMessage", () => {
  it("has words for each refusal code, the server's sentence for a 409, and a safe one without", () => {
    const said = (code: string, status = 409) => operatorErrorMessage(new ApiError(status, code, "Something went wrong."));
    expect(said("step_up_failed", 403)).toBe(STEP_UP_FAILED);
    expect(said("bad_request", 400)).toBe(CHECK_INPUT);
    expect(said("name_mismatch")).toBe(NAME_MISMATCH);
    expect(said("not_disabled")).toBe("Disable the organisation before deleting it.");
    expect(said("deleting")).toBe("This organisation is being deleted.");
    expect(said("taken")).toBe("That e-mail is already in use.");
    expect(said("not_found", 404)).toBe(ORG_NOT_FOUND);
    expect(said("step_up_locked", 403)).toBe(REFUSALS.locked);
    expect(said("locked", 403)).toMatch(/^Too many wrong codes/);
    expect(said("conflict")).toBe(CONFLICT); // the client's own fallback is never shown
    expect(said("constructor")).toBe(CONFLICT); // not a key of the map's prototype
    const purging = "The grace period is over: the organisation is being removed.";
    expect(operatorErrorMessage(new ApiError(409, "conflict", purging))).toBe(purging);
  });

  it("says what went wrong loading, in plain words", () => {
    expect(operatorErrorMessage(new ApiError(401, "not_signed_in", "x"))).toMatch(/session ended/);
    expect(operatorErrorMessage(new ApiError(429, "rate_limited", "x"))).toBe(TOO_MANY_ATTEMPTS);
    expect(operatorErrorMessage(new ApiError(0, "error", "x"))).toBe(NETWORK_FAILURE);
    expect(operatorErrorMessage(new TypeError("x"))).toBe(NETWORK_FAILURE);
    expect(operatorErrorMessage(new ApiError(503, "backend_unavailable", "x"))).toBe(OPERATOR_DOWN);
    expect(operatorErrorMessage(new Error("x"))).toBe(OPERATOR_DOWN);
  });
});

describe("an organisation in words", () => {
  it("names the status, with the delete date while it is being deleted", () => {
    expect(orgStatus({ status: "active", delete_after: null })).toEqual({ text: "Active", tone: "ready", note: null });
    expect(orgStatus({ status: "disabled", delete_after: null })).toEqual({ text: "Disabled", tone: "neutral", note: null });
    const deleting = { text: "Being deleted", tone: "danger", note: "Deleted on 13 Oct 2026" };
    expect(orgStatus({ status: "deleting", delete_after: "2026-10-13T08:20:00Z" })).toEqual(deleting);
    expect(orgStatus({ status: "deleting", delete_after: null }).note).toBeNull();
  });

  it("counts users per role, one or many, in Indian digit grouping", () => {
    const counts = ["1 admin", "0 reviewers", "1,20,000 members"];
    expect(userCounts({ admins: 1, reviewers: 0, members: 120000 })).toEqual(counts);
  });

  it("says when the last activity was, or that there was none", () => {
    expect(lastActivityText(null, NOW)).toBe("No activity yet");
    expect(lastActivityText("2026-10-06T08:15:00Z", NOW)).toBe("5 minutes ago");
  });
});

describe("deleting an organisation", () => {
  it("says the date, the cancel and the backups", () => {
    const said = "Everything is deleted on 13 Oct 2026. You can cancel until then. Backups keep data for 7 more days.";
    expect(deleteEffect(NOW)).toBe(said);
  });

  it("counts down the days left, never below nothing", () => {
    expect(deleteCountdown("2026-10-12T09:00:00Z", NOW)).toBe("6 days left");
    expect(deleteCountdown("2026-10-07T09:00:00Z", NOW)).toBe("1 day left");
    expect(deleteCountdown("2026-10-06T20:00:00Z", NOW)).toBe("Less than a day left");
    expect(deleteCountdown("2026-10-06T08:00:00Z", NOW)).toBe("Being removed now");
    expect(deleteCountdown(null, NOW)).toBeNull();
    expect(deleteCountdown("not a date", NOW)).toBeNull();
  });

  it("needs the name typed exactly, as the server compares it", () => {
    expect(nameError("Sharma Traders", "Sharma Traders")).toBeNull();
    expect(nameError("sharma traders", "Sharma Traders")).toBe(NAME_MISMATCH);
    expect(nameError("Sharma Traders ", "Sharma Traders")).toBe(NAME_MISMATCH);
    expect(nameError("", "Sharma Traders")).toBe("Type the organisation's name.");
  });
});

describe("the operator session", () => {
  it("leaves for sign-in, saying the session ended only when this tab had one", () => {
    expect(exitUrl("ended", true)).toBe("/operator/login?ended=1");
    expect(exitUrl("ended", false)).toBe("/operator/login");
    expect(exitUrl("signed_out", true)).toBe("/operator/login?signed_out=1");
  });

  it("goes to sign in on a 401 from /me, and shows Try again for anything else", () => {
    expect(meFailed(new ApiError(401, "not_signed_in", "x"))).toBe("signed_out");
    expect(meFailed(new ApiError(503, "backend_unavailable", "x"))).toBe("error");
    expect(meFailed(new TypeError("fetch failed"))).toBe("error");
  });

  it("times the session's end from expires_at, within one browser timer", () => {
    const expires = NOW / 1000 + 30 * 60;
    expect(expiryDelay(expires, NOW)).toBe(30 * 60 * 1000);
    expect(expiryDelay(NOW / 1000 - 5, NOW)).toBe(0);
    expect(expiryDelay(NOW / 1000 + 10 ** 9, NOW)).toBe(2 ** 31 - 1);
  });

  it("asks again after a check that failed for any reason but a 401", () => {
    expect(recheckAfter(new ApiError(401, "not_signed_in", "x"))).toBeNull(); // it has left already
    expect(recheckAfter(new TypeError("fetch failed"))).toBe(30_000);
    expect(recheckAfter(new ApiError(503, "backend_unavailable", "x"))).toBe(30_000);
  });

  it("signs out on the server, and still leaves when it says the session had ended", async () => {
    const calls: string[] = [];
    const leave = { leave: "/operator/login?signed_out=1" };
    expect(await signOut(async () => calls.push("logout"))).toEqual(leave);
    expect(calls).toEqual(["logout"]);
    const ended = async () => Promise.reject(new ApiError(401, "not_signed_in", "x"));
    expect(await signOut(ended)).toEqual(leave);
    const down = async () => Promise.reject(new ApiError(503, "backend_unavailable", "x"));
    expect(await signOut(down)).toEqual({ error: SIGN_OUT_FAILED });
    expect(await signOut(async () => Promise.reject(new TypeError("x")))).toEqual({ error: SIGN_OUT_FAILED });
  });
});

describe("closing the operator dialogs", () => {
  it("never closes while busy (the temporary password would be lost); a shown password only by a button", () => {
    const ways = ["button", "escape", "backdrop"] as const;
    const can = (state: { busy: boolean; secretShown?: boolean }) => ways.map((way) => canCloseDialog(state, way));
    expect([can({ busy: true }), can({ busy: true, secretShown: true })]).toEqual([[false, false, false], [false, false, false]]);
    expect([can({ busy: false }), can({ busy: false, secretShown: true })]).toEqual([[true, true, true], [true, false, false]]);
  });

  it("lets a late reply clear only its own dialog", () => {
    const [first, second] = [{ title: "Disable A?" }, { title: "Enable B?" }];
    expect([afterDone(first, first), afterDone(second, first), afterDone(null, first)]).toEqual([null, second, null]);
  });

  it("keeps the dialogs locked in the source, and the customer app's dialogs as before", () => {
    const dialog = read("components/documents/ActionDialog.tsx"); // no canClose: every way closes, as before
    for (const part of ["canClose = () => true", 'canClose("escape")', 'canClose("backdrop")', '!canClose("button")']) {
      expect(dialog, part).toContain(part);
    }
    expect(read("components/operator/ConfirmDialog.tsx")).toContain("canCloseDialog({ busy }, way)");
    expect(read("components/operator/CreateOrgDialog.tsx")).toContain("canCloseDialog({ busy, secretShown: created !== null }, way)");
    expect(read("components/operator/OrganisationsView.tsx")).toMatch(/afterDone\(open, changing\)[\s\S]*id=\{orgFocusId\(org\.id\)\} tabIndex=\{-1\}/);
  });
});

describe("search", () => {
  const orgs = [{ name: "Sharma Traders" }, { name: "Mehta & Sons" }, { name: "sharma exports" }];

  it("matches part of a name, in any case; an empty search keeps all", () => {
    expect(filterOrgs(orgs, " SHARMA ").map((o) => o.name)).toEqual(["Sharma Traders", "sharma exports"]);
    expect(filterOrgs(orgs, "")).toHaveLength(3);
    expect(filterOrgs(orgs, "zzz")).toEqual([]);
  });

  it("says how many are shown", () => {
    expect(searchSummary(3, 3, "")).toBe("3 organisations.");
    expect(searchSummary(1, 1, "  ")).toBe("1 organisation.");
    expect(searchSummary(2, 3, "sha")).toBe("2 of 3 organisations match.");
  });
});

describe("a new organisation", () => {
  it("needs a name, the first admin's e-mail and name, within the server's lengths", () => {
    const empty = validateNewOrg({ name: " ", admin_email: "", admin_name: "" });
    const words = { name: "Enter the organisation's name.", admin_email: "Enter the first admin's e-mail address." };
    expect(empty).toEqual({ ...words, admin_name: "Enter the first admin's name." });
    expect(firstInvalidOrgField(empty)).toBe("name");
    const long = validateNewOrg({ name: "x".repeat(201), admin_email: "a@b", admin_name: "y".repeat(101) });
    expect(long).toEqual({ name: "Use at most 200 characters.", admin_name: "Use at most 100 characters." });
    const bad = validateNewOrg({ name: "Org", admin_email: "nope", admin_name: "Asha" });
    expect(bad).toEqual({ admin_email: EMAIL_INVALID });
    expect(firstInvalidOrgField(bad)).toBe("admin_email");
    expect(validateNewOrg({ name: "Org", admin_email: " a@b.in ", admin_name: "Asha" })).toEqual({});
  });
});

describe("operator source guards", () => {
  it("shows the agreed words", () => {
    expect(read("components/operator/OperatorShell.tsx")).toContain("Kaagaz · Platform");
    expect(KEY_HELP).toBe("Your two-step key is given to you on the server when your account is made.");
    expect(read("app/operator/login/OperatorSignInForm.tsx")).toContain("KEY_HELP");
    expect(DISABLE_EFFECT).toBe("Its users are signed out now and its jobs stop.");
    expect(read("components/operator/confirms.ts")).toContain("DISABLE_EFFECT");
    expect(TEMPORARY_PASSWORD_NOTE).toBe("Give this to the admin privately. They must change it at first sign-in.");
    const create = read("components/operator/CreateOrgDialog.tsx");
    expect(create).toContain("TEMPORARY_PASSWORD_NOTE");
    expect(create).toContain("<CopyButton");
    const list = read("components/operator/OrganisationsView.tsx");
    const confirm = read("components/operator/ConfirmDialog.tsx");
    for (const label of ["Type the organisation name", "<ActionDialog", "A new 6-digit code from your app", 'role="alert"']) {
      expect(confirm, label).toContain(label);
    }
    const confirms = read("components/operator/confirms.ts");
    for (const label of ["Delete organisation", "Cancel deletion", "typeName: name", "deleteEffect(now)"]) {
      expect(confirms, label).toContain(label);
    }
    expect(read("components/operator/OrganisationsView.tsx")).toContain("deleteCountdown(");
    for (const label of ["Search by name", "Create organisation", "Uploaded size", "Last activity", 'role="status"']) {
      expect(list, label).toContain(label);
    }
  });

  it("never stores or logs anything, the temporary password included", () => {
    const offenders = OPERATOR_FILES.filter((file) =>
      /localStorage|sessionStorage|indexedDB|document\.cookie|console\.|caches\./.test(read(file)),
    );
    expect(offenders).toEqual([]);
    const holders = OPERATOR_FILES.filter((file) => read(file).includes("temporary_password"));
    expect(holders).toEqual(["components/operator/CreateOrgDialog.tsx"]);
  });

  it("tells other operator tabs on its own channel, never the customer one", () => {
    expect(OPERATOR_CHANNEL).toBe("kaagaz-operator-session");
    expect(OPERATOR_CHANNEL).not.toBe(SESSION_CHANNEL);
    const session = read("lib/operatorSession.tsx");
    expect(session).toContain("new BroadcastChannel(OPERATOR_CHANNEL)");
    const customer = /(SESSION_CHANNEL|announceSession|watchSession)|kaagaz-session"|new BroadcastChannel\((?!OPERATOR_CHANNEL\))/;
    expect(OPERATOR_FILES.filter((file) => customer.test(read(file)))).toEqual([]);
    const shell = read("components/operator/OperatorShell.tsx");
    expect(shell).toContain("signOut(operatorLogout)");
    expect(shell.indexOf("announceSignOut()")).toBeLessThan(shell.indexOf("leavePage(result.leave)"));
    expect(session).toContain("expiryDelay(expiresAt, Date.now())");
    // Any later 401 not_signed_in (lib/api.ts) leaves as "ended": ?ended=1 when this tab had a session.
    expect(session).toContain('onSessionEnded(() => setState({ kind: "signed_out", reason: "ended" }))');
    expect(session).toContain("leavePage(exitUrl(leaveFor, hadSession.current))");
  });

  it("sits outside the customer app, with its own session, and leaves only by full page loads", () => {
    expect(existsSync(join(ROOT, "app", "(app)", "operator"))).toBe(false);
    const layout = read("app/operator/(console)/layout.tsx");
    expect(layout).toContain("<OperatorSessionProvider>");
    expect(layout).not.toContain('from "@/lib/session"');
    expect(OPERATOR_FILES.filter((file) => /useRouter|useSession\(/.test(read(file)))).toEqual([]);
  });

  it("reaches the backend through the /api rewrite, so the operator cookie is first-party", async () => {
    const rewrites = await nextConfig.rewrites?.();
    const list = Array.isArray(rewrites) ? rewrites : (rewrites?.afterFiles ?? []);
    for (const path of ["/api/operator/login", "/api/operator/me", "/api/operator/orgs/o-1/disable"]) {
      const rule = list.find((r) => getPathMatch(r.source, { strict: true, removeUnnamedParams: true })(path));
      expect(rule?.destination, path).toMatch(/\/api\/:path\*$/);
    }
  });
});
