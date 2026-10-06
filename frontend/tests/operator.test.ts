// The operator (super admin) screens, PR 6: the pure rules, and source guards
// for what a render test would check (labels, the temporary password never
// stored, the operator area kept apart from the customer app).

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match";
import { describe, expect, it } from "vitest";
import { CODE_INVALID, CODE_MISSING } from "@/app/admin/login/adminSignIn";
import { EMAIL_INVALID, NETWORK_FAILURE, PASSWORD_MISSING, SIGN_IN_DOWN, TOO_MANY_ATTEMPTS } from "@/app/login/signIn";
import {
  CHECK_INPUT,
  DISABLE_EFFECT,
  KEY_HELP,
  OPERATOR_DOWN,
  OPERATOR_SIGN_IN_FAILED,
  ORG_NOT_FOUND,
  STEP_UP_FAILED,
  TEMPORARY_PASSWORD_NOTE,
  filterOrgs,
  firstInvalidOperator,
  firstInvalidOrgField,
  lastActivityText,
  operatorErrorMessage,
  operatorSignInError,
  orgStatus,
  searchSummary,
  userCounts,
  validateNewOrg,
  validateOperatorSignIn,
} from "@/components/operator/logic";
import { ApiError } from "@/lib/api";
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
    expect(validateOperatorSignIn("", "", "")).toEqual({
      email: "Enter your e-mail address.",
      password: PASSWORD_MISSING,
      code: CODE_MISSING,
    });
    expect(validateOperatorSignIn("op@monoedge.in", "pw", "123 456")).toEqual({
      email: null,
      password: null,
      code: null,
    });
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
  it("maps each refusal to plain words", () => {
    const step = new ApiError(403, "step_up_failed", "That two-step code was not accepted.");
    expect(operatorErrorMessage(step)).toBe(STEP_UP_FAILED);
    const deleting = "This organisation is being deleted.";
    expect(operatorErrorMessage(new ApiError(409, "conflict", deleting))).toBe(deleting);
    const taken = "That e-mail is already in use.";
    expect(operatorErrorMessage(new ApiError(409, "conflict", taken))).toBe(taken);
    expect(operatorErrorMessage(new ApiError(404, "not_found", "x"))).toBe(ORG_NOT_FOUND);
    expect(operatorErrorMessage(new ApiError(400, "bad_request", "x"))).toBe(CHECK_INPUT);
    expect(operatorErrorMessage(new ApiError(429, "rate_limited", "x"))).toBe(TOO_MANY_ATTEMPTS);
    expect(operatorErrorMessage(new ApiError(0, "error", "x"))).toBe(NETWORK_FAILURE);
    expect(operatorErrorMessage(new TypeError("x"))).toBe(NETWORK_FAILURE);
    expect(operatorErrorMessage(new ApiError(500, "internal_error", "x"))).toBe(OPERATOR_DOWN);
    expect(operatorErrorMessage(new ApiError(401, "not_signed_in", "x"))).toMatch(/session ended/);
  });
});

describe("an organisation in words", () => {
  it("names the status, with the delete date while it is being deleted", () => {
    expect(orgStatus({ status: "active", delete_after: null })).toEqual({ text: "Active", tone: "ready", note: null });
    expect(orgStatus({ status: "disabled", delete_after: null })).toEqual({ text: "Disabled", tone: "neutral", note: null });
    expect(orgStatus({ status: "deleting", delete_after: "2026-10-13T08:20:00Z" })).toEqual({
      text: "Being deleted",
      tone: "danger",
      note: "Deleted on 13 Oct 2026",
    });
    expect(orgStatus({ status: "deleting", delete_after: null }).note).toBeNull();
  });

  it("counts users per role, one or many, in Indian digit grouping", () => {
    expect(userCounts({ admins: 1, reviewers: 0, members: 120000 })).toEqual([
      "1 admin",
      "0 reviewers",
      "1,20,000 members",
    ]);
  });

  it("says when the last activity was, or that there was none", () => {
    expect(lastActivityText(null, NOW)).toBe("No activity yet");
    expect(lastActivityText("2026-10-06T08:15:00Z", NOW)).toBe("5 minutes ago");
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
    expect(empty).toEqual({
      name: "Enter the organisation's name.",
      admin_email: "Enter the first admin's e-mail address.",
      admin_name: "Enter the first admin's name.",
    });
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
    expect(read("components/operator/OrgStatusDialog.tsx")).toContain("DISABLE_EFFECT");
    expect(TEMPORARY_PASSWORD_NOTE).toBe("Give this to the admin privately. They must change it at first sign-in.");
    const create = read("components/operator/CreateOrgDialog.tsx");
    expect(create).toContain("TEMPORARY_PASSWORD_NOTE");
    expect(create).toContain("<CopyButton");
    const list = read("components/operator/OrganisationsView.tsx");
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

  it("sits outside the customer app, with its own session, and leaves only by full page loads", () => {
    expect(existsSync(join(ROOT, "app", "(app)", "operator"))).toBe(false);
    const layout = read("app/operator/(console)/layout.tsx");
    expect(layout).toContain("<OperatorSessionProvider>");
    expect(layout).not.toMatch(/\bSessionProvider\b(?! *}| *from)/);
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
