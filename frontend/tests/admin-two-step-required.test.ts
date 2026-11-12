// Require two-step for admins (#481, Tushar, 3 Nov 2026; 0068): the super admin's switch for one
// organisation, opened from its Manage menu, off by default, every flip asked first with a fresh
// code; and Account's Two-step sign-in, which offers no Turn off while the organisation requires
// it. Also the activity words for the new rows (#482's reads and the switch).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { describeAction, whoText } from "@/components/admin/logic";
import { ADMIN_TWO_STEP_HELP, OrgAdminTwoStepDialog, adminTwoStepConfirm } from "@/components/operator/AdminTwoStep";
import { MENU_HELP } from "@/components/operator/OrganisationsView";
import { TwoStepDialog } from "@/components/shell/TwoStepDialog";
import { REQUIRED_TEXT, mayTurnOff } from "@/components/shell/twoStepAccount";
import { getAdminTwoStep, setAdminTwoStep } from "@/lib/api";
import type { Me, Organisation } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const ORG = { id: "org-1", name: "Sharma Traders" } as Organisation;

const admin = (required: boolean | undefined): Me => ({
  user: {
    id: "u1",
    email: "ramesh@example.com",
    display_name: "Ramesh",
    role: "admin",
    must_change_password: false,
    totp_enabled: true,
    two_step_turned_off: false,
    ...(required === undefined ? {} : { two_step_required: required }),
    can_delete_forever: false,
    email_confirmed: true,
  },
  customer: { id: "c1", name: "Sharma Traders" },
  session: { scope: "admin" },
  providers: [],
});

afterEach(() => vi.unstubAllGlobals());

describe("the super admin's switch", () => {
  it("opens from the Manage menu, hidden while the organisation is being deleted", () => {
    const view = read("components/operator/OrganisationsView.tsx");
    expect(view).toContain('{ label: "Two-step for admins", description: MENU_HELP.twoStep, onSelect: onTwoStep }');
    expect(view.indexOf('org.status === "deleting"')).toBeLessThan(view.indexOf('label: "Two-step for admins"'));
    expect(MENU_HELP.twoStep).toBe("Require every admin here to sign in with a code from their phone.");
    const html = renderToStaticMarkup(createElement(OrgAdminTwoStepDialog, { org: ORG, onClose: () => {}, onDone: () => {} }));
    // Changed on purpose (QA U14, 4 Nov 2026): "admins of" reads right for any name, one ending in s too.
    expect(html).toContain("Two-step sign-in for admins of Sharma Traders");
    expect(html).toContain(ADMIN_TWO_STEP_HELP.replaceAll("'", "&#x27;"));
    expect(ADMIN_TWO_STEP_HELP).toBe(
      "Every admin of this organisation must sign in with a password and a 6-digit code. An admin who turned it off sets it up before they can do anything else, and no admin can turn it off.",
    );
  });

  it("asks first with a fresh code, in plain words each way", () => {
    expect(adminTwoStepConfirm(ORG, true)).toMatchObject({
      title: "Require two-step sign-in for admins of Sharma Traders?",
      // Every route refuses them until then (current_user's 403 mfa_enrol_required), not only the admin screens.
      effect: "An admin of Sharma Traders without two-step sign-in must set it up before they can use Kaagaz again. Members are not affected.",
      action: "Require it",
      danger: false,
      stepUp: true,
    });
    expect(adminTwoStepConfirm(ORG, false)).toMatchObject({
      title: "Stop requiring two-step sign-in for admins of Sharma Traders?",
      effect: "Admins of Sharma Traders can turn their own two-step sign-in off again. Anyone who has it on keeps it.",
      action: "Stop requiring it",
      done: "Two-step sign-in is no longer required for admins of Sharma Traders.",
      danger: true,
      stepUp: true,
    });
  });

  it("reads and saves in the agreed shapes", async () => {
    const sent: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response(JSON.stringify({ required: true }), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    await getAdminTwoStep("org-1");
    await setAdminTwoStep("org-1", true, "123456");
    expect(sent).toEqual([
      { url: "/api/operator/orgs/org-1/admin-two-step", method: "GET", body: undefined },
      { url: "/api/operator/orgs/org-1/admin-two-step", method: "PUT", body: { required: true, code: "123456" } },
    ]);
  });
});

describe("Account, while the organisation requires it", () => {
  it("offers no Turn off and says why; deny by default, only true locks it", () => {
    expect(mayTurnOff(admin(true))).toBe(false);
    expect(mayTurnOff(admin(false))).toBe(true);
    expect(mayTurnOff(admin(undefined))).toBe(true); // an older server: the server still refuses
    const locked = renderToStaticMarkup(createElement(TwoStepDialog, { me: admin(true), onClose: () => {}, onDone: () => {} }));
    expect(locked).toContain(REQUIRED_TEXT);
    expect(locked).not.toMatch(/Turn off|Current password/);
    const open = renderToStaticMarkup(createElement(TwoStepDialog, { me: admin(false), onClose: () => {}, onDone: () => {} }));
    expect(open).toContain("Turn off");
    expect(open).not.toContain(REQUIRED_TEXT);
  });
});

describe("the activity words", () => {
  it("say the new rows in plain words", () => {
    expect(describeAction("search.run")).toBe("Searched inside documents");
    expect(describeAction("usage.export")).toBe("Exported the AI usage");
    expect(describeAction("org.admin_two_step_on")).toBe("Two-step sign-in required for admins");
    expect(describeAction("org.admin_two_step_off")).toBe("Two-step sign-in no longer required for admins");
    expect(whoText({ action: "org.admin_two_step_on", actor_name: null, details: {} })).toBe("Kaagaz");
    // QA P2 (4 Nov 2026): a held file released after a reading pause; Kaagaz does it by itself.
    expect(describeAction("document.reading_released")).toBe("Reading started again after a pause");
    expect(whoText({ action: "document.reading_released", actor_name: null, details: {} })).toBe("Kaagaz");
  });
});
