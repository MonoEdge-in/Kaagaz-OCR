// Google sign-in per organisation and role (#507), the frontend (plan section 7, test 15): the
// words for each code, the limited session (sent to Ways to sign in, with the banner), the
// admin's "Ways to sign in" form and its checks, People's tag and reset line, and the super
// admin's switch, always asked first with a fresh code.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  DOMAIN_REFUSED,
  GOOGLE_OFF,
  PASSWORD_REFUSED,
  ROLE_REFUSED,
  WAITING,
  oidcErrorMessage,
} from "@/app/login/providerMessages";
import { ProviderButtons } from "@/app/login/ProviderButtons";
import { signInErrorMessage } from "@/app/login/signIn";
import { DOMAIN_UNCHECKED, LINK_REQUIRED, pageNotice, rulesText } from "@/components/account/signInWays";
import { LinkRequiredBanner } from "@/components/account/ConnectedBanner";
import {
  DOMAINS_HINT,
  DOMAINS_LABEL,
  DOMAIN_SHAPE,
  GOOGLE_OFF_TEXT,
  GOOGLE_ONLY_HINT,
  LOCKOUT_NO_LINK,
  LOCKOUT_OUTSIDE,
  ONLY_NEEDS_ALLOWED,
  PERSONAL_DOMAIN,
  TOO_MANY_DOMAINS,
  googleForm,
  googleRulesChange,
  googleRulesChanged,
  googleRulesProblem,
  googleSaveError,
  parseDomains,
  setAllowed,
  setOnly,
} from "@/components/admin/googleRules";
import {
  ADD_USER_GOOGLE_HINT,
  GOOGLE_NEEDED,
  RESET_DISCONNECTS,
  addUserGoogleHint,
  describeAction,
  googleTag,
  resetPasswordQuestion,
  whoText,
} from "@/components/admin/logic";
import { GoogleRulesFields } from "@/components/admin/SettingsPanel";
import {
  OFF_HINT,
  OrgGoogleSignInDialog,
  PlatformGoogleSignIn,
  choiceLabels,
  inForceText,
  orgGoogleConfirm,
  platformGoogleConfirm,
} from "@/components/operator/GoogleSignIn";
import { MENU_HELP } from "@/components/operator/OrganisationsView";
import { WAYS_PATH, appChrome, appGate } from "@/components/shell/gate";
import {
  ApiError,
  getOrgGoogleSignIn,
  onGoogleLinkRequired,
  setGoogleSignIn,
  setOrgGoogleSignIn,
  updateSettings,
} from "@/lib/api";
import type { AdminUser, GoogleRules, Me, OrgGoogleSignIn, Organisation } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const ORG = { id: "org-1", name: "Sharma Traders" } as Organisation;
const html = (text: string) => text.replaceAll("'", "&#x27;");

afterEach(() => {
  vi.unstubAllGlobals();
  onGoogleLinkRequired(null);
});

describe("the sign-in page's words for each code", () => {
  it("says each Google refusal in the plan's words", () => {
    expect(oidcErrorMessage("google_off")).toBe(GOOGLE_OFF);
    expect(GOOGLE_OFF).toBe("Google sign-in is off for your organisation. Sign in with your email and password.");
    expect(oidcErrorMessage("role_refused")).toBe(
      "Your organisation doesn't use Google sign-in for your role. Sign in with your email and password.",
    );
    expect(ROLE_REFUSED).toBe(oidcErrorMessage("role_refused"));
    expect(oidcErrorMessage("domain_refused")).toBe(
      "That Google account isn't from your organisation's domain. Choose your work account.",
    );
    expect(DOMAIN_REFUSED).toBe(oidcErrorMessage("domain_refused"));
    expect(oidcErrorMessage("waiting")).toBe(
      "Your Google account is waiting for your admin to approve it. Until then, sign in with your email and password.",
    );
    expect(WAITING).toBe(oidcErrorMessage("waiting"));
  });

  it("says a right password refused for a Google-only role, and nothing else changes", () => {
    const refused = new ApiError(403, "password_refused", "Use Google.");
    expect(signInErrorMessage(refused)).toBe("Your organisation asks you to sign in with Google. Use Continue with Google.");
    expect(PASSWORD_REFUSED).toBe(signInErrorMessage(refused));
    expect(signInErrorMessage(new ApiError(403, "forbidden", "No."))).toBe(
      "Sign-in is not working right now. Try again in a minute.",
    );
  });

  it("shows waiting as a calm notice, a refusal as an alert", () => {
    const waiting = renderToStaticMarkup(
      createElement(ProviderButtons, { next: "/dashboard", error: "waiting", providers: ["google"] }),
    );
    expect(waiting).toContain('role="status"');
    expect(waiting).not.toContain('role="alert"');
    expect(waiting).toContain(html(WAITING));
    const refused = renderToStaticMarkup(
      createElement(ProviderButtons, { next: "/dashboard", error: "domain_refused", providers: ["google"] }),
    );
    expect(refused).toContain('role="alert"');
    expect(refused).toContain(html(DOMAIN_REFUSED));
  });
});

describe("Ways to sign in", () => {
  it("says why connecting was refused", () => {
    expect(pageNotice({ error: "google_off" })).toEqual({
      kind: "error",
      text: "Google sign-in is off for your organisation.",
    });
    expect(pageNotice({ error: "role_refused" })).toEqual({
      kind: "error",
      text: "Your organisation doesn't use Google sign-in for your role.",
    });
    expect(pageNotice({ error: "domain_refused" })).toEqual({ kind: "error", text: DOMAIN_REFUSED });
  });

  it("shows the organisation's rules for this person", () => {
    expect(rulesText(undefined, false)).toBeNull();
    expect(rulesText({ google_only: false, domains: [] }, false)).toBeNull();
    expect(rulesText({ google_only: true, domains: [] }, false)).toBe("Your organisation asks you to sign in with Google.");
    // The limited session's banner already says so: only the domains are added.
    expect(rulesText({ google_only: true, domains: [] }, true)).toBeNull();
    expect(rulesText({ google_only: false, domains: ["sharmatraders.com"] }, false)).toBe(
      "Use a Google account from sharmatraders.com.",
    );
    expect(rulesText({ google_only: true, domains: ["a.com", "b.in", "c.org"] }, true)).toBe(
      "Use a Google account from a.com, b.in or c.org.",
    );
  });

  it("shows the limited session's banner in the plan's words", () => {
    expect(LINK_REQUIRED).toBe("Your organisation asks you to sign in with Google. Connect your Google account to continue.");
    const banner = renderToStaticMarkup(createElement(LinkRequiredBanner, { unchecked: false }));
    expect(banner).toContain('role="status"');
    expect(banner).toContain(LINK_REQUIRED);
    const shell = read("components/shell/AppShell.tsx");
    expect(shell).toContain(
      "{me.user.google_link_required && <LinkRequiredBanner unchecked={me.user.google_unchecked === true} />}",
    );
    const page = read("components/account/WaysToSignIn.tsx");
    expect(page).toContain("rulesText(ways.rules, me.user.google_link_required === true)");
  });
});

describe("a Google account connected before its domain could be checked (#507 review)", () => {
  it("asks for one Google sign-in, not a new connection, in the review's words", () => {
    expect(DOMAIN_UNCHECKED).toBe("Sign in with Google once so Kaagaz can check its domain.");
    const banner = renderToStaticMarkup(createElement(LinkRequiredBanner, { unchecked: true }));
    expect(banner).toContain(DOMAIN_UNCHECKED);
    expect(banner).not.toContain(LINK_REQUIRED);
  });

  it("tags the person in People with the same words", () => {
    const person = (extra: Partial<AdminUser>) => ({ ...extra }) as AdminUser;
    expect(googleTag(person({ google_needed: true, google_unchecked: true }))).toBe(DOMAIN_UNCHECKED);
    expect(googleTag(person({ google_needed: true, google_unchecked: false }))).toBe(GOOGLE_NEEDED);
    expect(googleTag(person({ google_needed: true }))).toBe(GOOGLE_NEEDED);
    expect(googleTag(person({ google_needed: false }))).toBeNull();
    expect(googleTag(person({}))).toBeNull();
    expect(read("components/admin/UsersPanel.tsx")).toContain("const tag = googleTag(user);");
  });
});

describe("the limited session (a Google-only person with no passing account)", () => {
  const me = (extra: Partial<Me["user"]> = {}, required?: boolean): Me => ({
    user: {
      id: "u1",
      email: "asha@sharmatraders.com",
      display_name: "Asha",
      role: "member",
      must_change_password: false,
      totp_enabled: false,
      two_step_turned_off: false,
      can_delete_forever: false,
      email_confirmed: true,
      ...extra,
      // Inside user, as the server sends it (backend api/views.py me_json).
      ...(required === undefined ? {} : { google_link_required: required }),
    },
    customer: { id: "c1", name: "Sharma Traders" },
    session: { scope: "app" },
    providers: ["google"],
  });

  it("is sent to Ways to sign in; a new password and two-step set-up come first", () => {
    expect(WAYS_PATH).toBe("/settings/sign-in");
    expect(appGate(me({}, true))).toBe("connect_google");
    expect(appGate(me({}, false))).toBe("app");
    expect(appGate(me())).toBe("app");
    expect(appGate(me({ must_change_password: true }, true))).toBe("set_password");
    expect(appGate(me({ role: "admin" }, true))).toBe("set_two_step");
  });

  it("shows no app links, search or Upload: only Ways to sign in and the account menu (#507 review)", () => {
    expect(appChrome(me({}, true))).toBe(false);
    expect(appChrome(me({}, false))).toBe(true);
    expect(appChrome(me())).toBe(true);
    const shell = read("components/shell/AppShell.tsx");
    expect(shell).toContain("const chrome = appChrome(me);");
    // Changed on purpose (PRD 7 PR 11c, #604): Management's Access activity link.
    expect(shell).toContain(
      "const links = chrome ? navLinks(me.user.role, sessionScope(me), me.access?.management === true) : [];",
    );
    expect(shell.match(/\{chrome && \(/g)?.length).toBe(3); // the menu button, search and Upload, the tab bar
    expect(shell).toContain("<AccountMenu me={me} onChanged={refresh} />");
  });

  it("never shows another screen: the app sends the person there first", () => {
    const session = read("lib/session.tsx");
    expect(session).toContain('if (gate !== "connect_google" || pathname === WAYS_PATH) return;');
    expect(session).toContain('if (gate !== "mfa_enrol" || pathname === WAYS_PATH) return;');
    expect(session).toContain("router.replace(WAYS_PATH);");
    expect(session).toContain('((gate === "connect_google" || gate === "mfa_enrol") && pathname !== WAYS_PATH)');
    expect(session).toContain("Taking you to Ways to sign in…");
  });

  it("hears a 403 google_link_required from any reply, so the app asks who is signed in again", async () => {
    const heard = vi.fn();
    onGoogleLinkRequired(heard);
    vi.stubGlobal(
      "fetch",
      async () =>
        new Response(JSON.stringify({ error: { code: "google_link_required", message: "Connect Google." } }), {
          status: 403,
          headers: { "Content-Type": "application/json" },
        }),
    );
    await expect(getOrgGoogleSignIn("org-1")).rejects.toMatchObject({ code: "google_link_required" });
    expect(heard).toHaveBeenCalledTimes(1);
    expect(read("lib/session.tsx")).toContain("onGoogleLinkRequired(");
  });
});

describe("the admin's Ways to sign in", () => {
  const saved: GoogleRules = {
    available: true,
    allowed_roles: ["admin", "reviewer", "member"],
    only_roles: [],
    domains: [],
  };

  it("reads domains typed with commas, spaces or new lines, in lower case, once each", () => {
    expect(parseDomains("")).toEqual([]);
    expect(parseDomains(" SharmaTraders.com, sharma.in\nsharmatraders.com  @gupta.co.in ")).toEqual([
      "sharmatraders.com",
      "sharma.in",
      "gupta.co.in",
    ]);
  });

  it("checks the form before saving, in the plan's words", () => {
    const form = googleForm(saved);
    expect(googleRulesProblem(form)).toBeNull();
    expect(googleRulesProblem({ ...form, only: ["member"], allowed: ["admin"] })).toEqual({
      field: "roles",
      message: "A role that uses Google only must also be allowed to use Google.",
    });
    expect(ONLY_NEEDS_ALLOWED).toBe("A role that uses Google only must also be allowed to use Google.");
    for (const bad of ["sharmatraders", "sharma traders.com", "http://sharma.com", "sharma..com", "-sharma.com"]) {
      expect(googleRulesProblem({ ...form, domains: bad }), bad).toEqual({ field: "domains", message: DOMAIN_SHAPE });
    }
    expect(DOMAIN_SHAPE).toBe("Enter a domain like sharmatraders.com.");
    for (const personal of ["gmail.com", "GoogleMail.com", "yahoo.co.in", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "icloud.com", "rediffmail.com", "proton.me", "protonmail.com"]) {
      expect(googleRulesProblem({ ...form, domains: `sharma.in, ${personal}` }), personal).toEqual({
        field: "domains",
        message: PERSONAL_DOMAIN,
      });
    }
    expect(PERSONAL_DOMAIN).toBe("Gmail and other personal email domains can't be used. Enter your company's own domain.");
    const eleven = Array.from({ length: 11 }, (_, n) => `d${n}.com`).join(", ");
    expect(googleRulesProblem({ ...form, domains: eleven })).toEqual({ field: "domains", message: TOO_MANY_DOMAINS });
    expect(TOO_MANY_DOMAINS).toBe("Add at most 10 domains.");
    expect(googleRulesProblem({ ...form, domains: eleven.split(", ").slice(0, 10).join(", ") })).toBeNull();
  });

  it("keeps Google only inside Google allowed", () => {
    let form = googleForm(saved);
    form = setOnly(form, "admin", true);
    expect(form.only).toEqual(["admin"]);
    form = setAllowed(form, "admin", false);
    expect(form.allowed).toEqual(["reviewer", "member"]);
    expect(form.only).toEqual([]); // not allowed any more: not Google only either
    form = setAllowed(form, "admin", true);
    expect(form.allowed).toEqual(["admin", "reviewer", "member"]); // always in role order
  });

  it("sends all three rules, and only when they changed", () => {
    const form = googleForm({ ...saved, domains: ["sharma.in"], only_roles: ["member"] });
    expect(form.domains).toBe("sharma.in");
    expect(googleRulesChanged(saved, googleForm(saved))).toBe(false);
    expect(googleRulesChanged(saved, { ...googleForm(saved), domains: " " })).toBe(false);
    expect(googleRulesChanged(saved, form)).toBe(true);
    expect(googleRulesChange(form)).toEqual({
      allowed_roles: ["admin", "reviewer", "member"],
      only_roles: ["member"],
      domains: ["sharma.in"],
    });
  });

  it("says a lock-out refusal in its own words", () => {
    expect(LOCKOUT_NO_LINK).toBe(
      "Connect your own Google account first, in Account, Ways to sign in. Then you can make Google the only way in for admins.",
    );
    expect(LOCKOUT_OUTSIDE).toBe(
      "Your own Google account isn't from these domains, so you'd be locked out. Add its domain, or connect one that is.",
    );
    expect(googleSaveError(new ApiError(409, "google_lockout", LOCKOUT_OUTSIDE))).toBe(LOCKOUT_OUTSIDE);
    expect(googleSaveError(new ApiError(409, "google_lockout", ""))).toBe(LOCKOUT_NO_LINK);
    expect(googleSaveError(new ApiError(409, "google_off", "x"))).toBe("Google sign-in is off for your organisation.");
    expect(googleSaveError(new ApiError(400, "bad_request", "Add at most 10 domains."))).toBe("Add at most 10 domains.");
  });

  it("shows each role's two choices, the hints, and the off state", () => {
    const form = setOnly(googleForm(saved), "admin", true);
    const on = renderToStaticMarkup(
      createElement(GoogleRulesFields, { rules: saved, form, problem: null, onChange: () => {} }),
    );
    for (const role of ["Admins", "Reviewers", "Members"]) expect(on).toMatch(new RegExp(`<legend[^>]*>${role}</legend>`));
    expect(on.match(/Google allowed/g)).toHaveLength(3);
    expect(on.match(/Google only/g)?.length).toBeGreaterThanOrEqual(3);
    expect(on).toContain(html(GOOGLE_ONLY_HINT));
    expect(GOOGLE_ONLY_HINT).toBe(
      "Once their Google account is connected, their password stops working for sign-in. Until then it only lets them connect Google.",
    );
    expect(on).toContain(DOMAINS_LABEL);
    expect(DOMAINS_LABEL).toBe("Company domains (optional)");
    expect(DOMAINS_HINT).toMatch(/^Only Google accounts from these domains can be used\. Leave empty to allow any\./);
    expect(on).toContain('id="google-only-admin"');
    const off = renderToStaticMarkup(
      createElement(GoogleRulesFields, { rules: { ...saved, available: false }, form, problem: null, onChange: () => {} }),
    );
    expect(off).toContain(GOOGLE_OFF_TEXT);
    expect(GOOGLE_OFF_TEXT).toBe("Google sign-in is off for your organisation. Everyone signs in with their email and password.");
    expect(off).not.toContain("Google allowed");
    const wrong = renderToStaticMarkup(
      createElement(GoogleRulesFields, {
        rules: saved,
        form,
        problem: { field: "domains", message: DOMAIN_SHAPE },
        onChange: () => {},
      }),
    );
    expect(wrong).toContain('aria-invalid="true"');
    expect(wrong).toContain('role="alert"');
  });

  it("saves the rules with the form's one Save", async () => {
    const sent: unknown[] = [];
    vi.stubGlobal("fetch", async (_url: string, init: RequestInit) => {
      sent.push(JSON.parse(String(init.body)));
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    });
    await updateSettings(null, undefined, { allowed_roles: ["admin"], only_roles: ["admin"], domains: ["sharma.in"] });
    await updateSettings(25);
    expect(sent).toEqual([
      { max_upload_mb: null, google: { allowed_roles: ["admin"], only_roles: ["admin"], domains: ["sharma.in"] } },
      { max_upload_mb: 25 },
    ]);
  });
});

describe("People", () => {
  it("tags who still needs to connect Google, and says a reset disconnects Google", () => {
    expect(GOOGLE_NEEDED).toBe("Needs to connect Google");
    expect(read("components/admin/UsersPanel.tsx")).toContain("{tag && (");
    expect(RESET_DISCONNECTS).toBe("This also disconnects their Google account.");
    expect(resetPasswordQuestion("Asha", ["password", "google"])).toBe(
      "Reset Asha's password? They are signed out everywhere and must choose a new one. This also disconnects their Google account.",
    );
    expect(resetPasswordQuestion("Asha", ["password"])).toBe(
      "Reset Asha's password? They are signed out everywhere and must choose a new one.",
    );
    expect(resetPasswordQuestion("Asha", undefined)).toBe(resetPasswordQuestion("Asha", ["password"]));
  });
});

describe("the activity log's words for #507's actions", () => {
  it("names each in plain words, and the super admin's switch as Kaagaz's", () => {
    expect(describeAction("settings.google_rules")).toBe("Changed the Google sign-in rules");
    expect(describeAction("settings.google_rules_refused")).toBe(
      "Google sign-in rules not saved: they would have locked you out",
    );
    expect(describeAction("auth.identity_link_refused")).toBe("A Google account couldn't be connected");
    expect(describeAction("auth.identity_reapproval")).toBe("A Google account waits for approval again");
    expect(describeAction("org.google_signin_set")).toBe("Google sign-in changed for the organisation");
    expect(whoText({ action: "org.google_signin_set", actor_name: null, details: {} })).toBe("Kaagaz");
  });
});

describe("Add a user (plan section 5)", () => {
  const rules: GoogleRules = {
    available: true,
    allowed_roles: ["admin", "member"],
    only_roles: [],
    domains: [],
  };

  it("says how a new person signs in with Google only while Google is on for the chosen role", () => {
    expect(ADD_USER_GOOGLE_HINT).toBe(
      "Your organisation signs in with Google for this role. After they choose a password, they connect their Google account.",
    );
    expect(addUserGoogleHint(rules, "member")).toBe(ADD_USER_GOOGLE_HINT);
    expect(addUserGoogleHint(rules, "admin")).toBe(ADD_USER_GOOGLE_HINT);
    expect(addUserGoogleHint(rules, "reviewer")).toBeNull(); // not allowed for this role
    expect(addUserGoogleHint({ ...rules, available: false }, "member")).toBeNull(); // the super admin turned it off
    expect(addUserGoogleHint(undefined, "member")).toBeNull(); // the server's Google sign-in is off
  });

  it("reads the rules from Settings and shows the hint under the role", () => {
    const panel = read("components/admin/UsersPanel.tsx");
    expect(panel).toContain("addUserGoogleHint(googleRules, input.role)");
    expect(panel).toContain("getSettings()");
  });
});

describe("the super admin's Google sign-in", () => {
  it("opens from the Manage menu, with its plain line", () => {
    expect(MENU_HELP.google).toBe("Let this organisation's people sign in with Google, or turn it off.");
    const view = read("components/operator/OrganisationsView.tsx");
    expect(view).toContain('{ label: "Google sign-in", description: MENU_HELP.google, onSelect: onGoogle }');
    expect(view.indexOf('org.status === "deleting"')).toBeLessThan(view.indexOf('label: "Google sign-in"'));
  });

  it("offers following the platform, On and Off, and shows the admin's rules read-only", () => {
    expect(choiceLabels(true)).toEqual(["Follow the platform (On)", "On", "Off"]);
    expect(choiceLabels(false)[0]).toBe("Follow the platform (Off)");
    expect(OFF_HINT).toBe(
      "Off: everyone signs in with their password, even where admins chose Google only. Connected accounts are kept. People signed in with Google are signed out.",
    );
    const dialog = renderToStaticMarkup(createElement(OrgGoogleSignInDialog, { org: ORG, onClose: () => {}, onDone: () => {} }));
    expect(dialog).toContain("Google sign-in for Sharma Traders");
    expect(dialog).toContain(html(MENU_HELP.google));
    const source = read("components/operator/GoogleSignIn.tsx");
    expect(source).toContain("rolesText(value.google_only_roles)");
    expect(source).toContain("value.domains");
  });

  const value = (on: boolean | null, platformOn: boolean, effective?: boolean): OrgGoogleSignIn => ({
    on,
    platform_on: platformOn,
    effective: effective ?? (on ?? platformOn),
    google_only_roles: [],
    domains: [],
  });

  it("shows the value in force, and says when the server has Google sign-in off (#507 review)", () => {
    expect(inForceText(value(true, false))).toBe("In force: On.");
    expect(inForceText(value(null, false))).toBe("In force: Off.");
    expect(inForceText(value(null, true, false))).toBe("In force: Off. Google sign-in is off on this server.");
    expect(inForceText(value(false, true, false))).toBe("In force: Off.");
    expect(read("components/operator/GoogleSignIn.tsx")).toContain("{inForceText(value)}");
  });

  it("says people are signed out only when the value in force changes (#507 review)", () => {
    const signedOut = /signed out/;
    expect(orgGoogleConfirm(ORG, false, value(true, false)).effect).toMatch(signedOut); // On to Off
    expect(orgGoogleConfirm(ORG, false, value(null, false)).effect).not.toMatch(signedOut); // Off stays Off
    expect(orgGoogleConfirm(ORG, true, value(null, true)).effect).not.toMatch(signedOut); // On stays On
    expect(orgGoogleConfirm(ORG, true, value(false, true)).effect).toMatch(signedOut); // Off to On
    expect(orgGoogleConfirm(ORG, null, value(true, true)).effect).not.toMatch(signedOut);
    expect(orgGoogleConfirm(ORG, null, value(false, true)).effect).toMatch(signedOut);
    // The server's switch off: the organisation's value still changes, and its sign-out still happens.
    expect(orgGoogleConfirm(ORG, false, value(true, true, false)).effect).toMatch(signedOut);
  });

  it("asks first, with a fresh code, before every change", () => {
    const off = orgGoogleConfirm(ORG, false, value(null, true));
    expect(off).toMatchObject({
      title: "Turn Google sign-in off for Sharma Traders?",
      action: "Turn off",
      done: "Google sign-in is off for Sharma Traders.",
      danger: true,
      stepUp: true,
    });
    expect(off.effect).toContain("Connected accounts are kept.");
    expect(orgGoogleConfirm(ORG, true, value(null, false))).toMatchObject({
      title: "Turn Google sign-in on for Sharma Traders?",
      action: "Turn on",
      danger: false,
      stepUp: true,
    });
    expect(orgGoogleConfirm(ORG, null, value(false, true))).toMatchObject({
      title: "Make Sharma Traders follow the platform for Google sign-in?",
      done: "Sharma Traders follows the platform for Google sign-in (On).",
      stepUp: true,
    });
    expect(platformGoogleConfirm(false)).toMatchObject({
      title: "Turn Google sign-in off for the platform?",
      action: "Turn off",
      danger: true,
      stepUp: true,
    });
    expect(platformGoogleConfirm(true)).toMatchObject({ action: "Turn on", danger: false, stepUp: true });
  });

  it("keeps the platform's default on the Timeout setup tab", () => {
    const console = read("components/operator/OperatorConsole.tsx");
    expect(console).toMatch(/active === "timeout" && \(\s*<>\s*<SessionTimeoutView \/>\s*<PlatformGoogleSignIn \/>/);
    expect(renderToStaticMarkup(createElement(PlatformGoogleSignIn))).toContain("Google sign-in");
  });

  it("reads and saves in the agreed shapes", async () => {
    const sent: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    });
    await getOrgGoogleSignIn("org-1");
    await setOrgGoogleSignIn("org-1", null, "123456");
    await setGoogleSignIn(false, "654321");
    expect(sent).toEqual([
      { url: "/api/operator/orgs/org-1/google-signin", method: "GET", body: undefined },
      { url: "/api/operator/orgs/org-1/google-signin", method: "PUT", body: { on: null, code: "123456" } },
      { url: "/api/operator/google-signin", method: "PUT", body: { on: false, code: "654321" } },
    ]);
  });
});
