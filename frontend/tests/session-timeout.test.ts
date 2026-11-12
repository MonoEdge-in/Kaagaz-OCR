// The sign-in idle timeout (0040): the Timeout setup tab and one organisation's dialog, the 24
// hour choices plus no idle limit (or follow the platform), the confirm with a fresh code, and the API call shapes.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OPERATOR_TABS } from "@/components/operator/OperatorConsole";
import { OrgSessionTimeoutDialog, SessionTimeoutView, TimeoutForm, noneLabel, timeoutConfirm } from "@/components/operator/SessionTimeout";
import { getSessionTimeout, setSessionTimeout } from "@/lib/api";
import type { Organisation } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const ORG = { id: "org-1", name: "Sharma Traders" } as Organisation;

afterEach(() => vi.unstubAllGlobals());

describe("Timeout setup", () => {
  it("is the tab after Feature switches", () => {
    // Changed on purpose (0053): Speed comes after it.
    expect(OPERATOR_TABS.map((tab) => tab.label).slice(-3, -1)).toEqual(["Feature switches", "Timeout setup"]);
  });

  it("explains itself and offers 1 to 24 hours plus no idle limit", () => {
    expect(renderToStaticMarkup(createElement(SessionTimeoutView))).toContain(
      "How long organisation admins and members stay signed in with no activity (up to 24 hours).",
    );
    const none = noneLabel({ hours: 3, default_hours: 12 }, false);
    expect(none).toBe("No idle limit (sign-in lasts 12 hours)"); // the session's length, not an idle timeout
    const html = renderToStaticMarkup(createElement(TimeoutForm, { saved: 3, none, onSave: () => {} }));
    const options = [...html.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((m) => [m[1], m[2]]);
    expect(options).toHaveLength(25);
    expect(options[0]).toEqual(["", "No idle limit (sign-in lasts 12 hours)"]);
    expect(options[1]).toEqual(["1", "1 hour"]);
    expect(options[2]).toEqual(["2", "2 hours"]);
    expect(options[24]).toEqual(["24", "24 hours"]);
    expect(html).toMatch(/<option value="3" selected="">3 hours<\/option>/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save<\/button>/); // nothing changed yet
    expect(html).not.toMatch(/[$₹]|price|cost|budget|spend/i);
  });

  it("asks first with a fresh code", () => {
    const none = "No idle limit (sign-in lasts 12 hours)";
    expect(timeoutConfirm(2, none)).toMatchObject({
      title: "Set the sign-in timeout to 2 hours?",
      effect: expect.stringContaining("sign in again after 2 hours with no activity"),
      stepUp: true,
    });
    const off = timeoutConfirm(null, none);
    expect(off.effect).toBe("Organisation admins and members are not signed out for being inactive. Each sign-in lasts its usual length.");
    const follow = timeoutConfirm(null, "Follow the platform (8 hours)", ORG);
    expect(follow).toMatchObject({
      title: "Set the sign-in timeout for Sharma Traders to Follow the platform (8 hours)?",
      effect: "Sharma Traders's admins and members follow the platform's setting from now on.",
      stepUp: true,
    });
    for (const each of [off, follow]) expect(each.effect).not.toMatch(/sign in again after/);
  });
});

describe("one organisation's session timeout", () => {
  it("opens from the Manage menu, hidden while the organisation is being deleted", () => {
    const view = read("components/operator/OrganisationsView.tsx");
    // Changed on purpose (plain words, 25 Oct 2026): each Manage item has a line saying what it does.
    expect(view).toContain('{ label: "Session timeout", description: MENU_HELP.timeout, onSelect: onTimeout }');
    // In the same list as AI settings and Feature switches, which a deleting organisation skips.
    expect(view.indexOf('org.status === "deleting"')).toBeLessThan(view.indexOf('label: "Session timeout"'));
    // Changed on purpose (#468 review, UX10): the page's Toast gets each change too (onDone).
    const html = renderToStaticMarkup(createElement(OrgSessionTimeoutDialog, { org: ORG, onClose: () => {}, onDone: () => {} }));
    expect(html).toContain("Session timeout for Sharma Traders");
  });

  it("offers Follow the platform with the platform's real value, or no idle limit", () => {
    expect(noneLabel({ hours: null, platform_hours: null, default_hours: 12 }, true)).toBe("Follow the platform (no idle limit)");
    const none = noneLabel({ hours: null, platform_hours: 8, default_hours: 12 }, true);
    const html = renderToStaticMarkup(createElement(TimeoutForm, { saved: null, none, onSave: () => {} }));
    expect(html).toMatch(/<option value="" selected="">Follow the platform \(8 hours\)<\/option>/);
  });
});

describe("the API calls", () => {
  it("read and save the platform's and one organisation's in the agreed shapes", async () => {
    const sent: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(JSON.stringify({ hours: null, default_hours: 12 }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    await getSessionTimeout();
    await setSessionTimeout(4, "123456");
    await getSessionTimeout("org-1");
    await setSessionTimeout(null, "654321", "org-1");
    expect(sent).toEqual([
      { url: "/api/operator/session-timeout", method: "GET", body: undefined },
      { url: "/api/operator/session-timeout", method: "PUT", body: { hours: 4, code: "123456" } },
      { url: "/api/operator/orgs/org-1/session-timeout", method: "GET", body: undefined },
      { url: "/api/operator/orgs/org-1/session-timeout", method: "PUT", body: { hours: null, code: "654321" } },
    ]);
  });
});
