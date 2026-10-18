// The sign-in idle timeout (0040): the Timeout setup tab and one organisation's dialog, the 24
// hour choices plus the default, the confirm with a fresh code, and the API call shapes.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { OPERATOR_TABS } from "@/components/operator/OperatorConsole";
import { OrgSessionTimeoutDialog, SessionTimeoutView, TimeoutForm, timeoutConfirm } from "@/components/operator/SessionTimeout";
import { getSessionTimeout, setSessionTimeout } from "@/lib/api";
import type { Organisation } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const ORG = { id: "org-1", name: "Sharma Traders" } as Organisation;

afterEach(() => vi.unstubAllGlobals());

describe("Timeout setup", () => {
  it("is the tab after Feature switches", () => {
    expect(OPERATOR_TABS.map((tab) => tab.label).slice(-2)).toEqual(["Feature switches", "Timeout setup"]);
  });

  it("explains itself and offers 1 to 24 hours plus the default", () => {
    expect(renderToStaticMarkup(createElement(SessionTimeoutView))).toContain(
      "How long organisation admins and members stay signed in with no activity (up to 24 hours).",
    );
    const html = renderToStaticMarkup(createElement(TimeoutForm, { saved: 3, none: "Default (12 hours)", onSave: () => {} }));
    const options = [...html.matchAll(/<option value="([^"]*)"[^>]*>([^<]*)<\/option>/g)].map((m) => [m[1], m[2]]);
    expect(options).toHaveLength(25);
    expect(options[0]).toEqual(["", "Default (12 hours)"]);
    expect(options[1]).toEqual(["1", "1 hour"]);
    expect(options[2]).toEqual(["2", "2 hours"]);
    expect(options[24]).toEqual(["24", "24 hours"]);
    expect(html).toMatch(/<option value="3" selected="">3 hours<\/option>/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save<\/button>/); // nothing changed yet
    expect(html).not.toMatch(/[$₹]|price|cost|budget|spend/i);
  });

  it("asks first with a fresh code", () => {
    expect(timeoutConfirm(2, "Default (12 hours)")).toMatchObject({
      title: "Set the sign-in timeout to 2 hours?",
      stepUp: true,
    });
    expect(timeoutConfirm(null, "Follow the platform (8 hours)", ORG)).toMatchObject({
      title: "Set the sign-in timeout for Sharma Traders to Follow the platform (8 hours)?",
      stepUp: true,
    });
  });
});

describe("one organisation's session timeout", () => {
  it("opens from the Manage menu, hidden while the organisation is being deleted", () => {
    const view = read("components/operator/OrganisationsView.tsx");
    expect(view).toContain('{ label: "Session timeout", onSelect: onTimeout }');
    // In the same list as AI settings and Feature switches, which a deleting organisation skips.
    expect(view.indexOf('org.status === "deleting"')).toBeLessThan(view.indexOf('label: "Session timeout"'));
    const html = renderToStaticMarkup(createElement(OrgSessionTimeoutDialog, { org: ORG, onClose: () => {} }));
    expect(html).toContain("Session timeout for Sharma Traders");
  });

  it("offers Follow the platform in place of the default", () => {
    const html = renderToStaticMarkup(createElement(TimeoutForm, { saved: null, none: "Follow the platform (8 hours)", onSave: () => {} }));
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
