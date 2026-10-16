// What each change on the operator screens says and does, for ConfirmDialog.
// Taking something away (disable an organisation, delete it, cancel its
// deletion, reset an admin's two-step) needs a fresh code (step-up), as the
// server does; giving it back (enable, unlock) and disabling an admin do not.

import {
  cancelDelete,
  changeAdmin,
  disableOrg,
  enableOrg,
  scheduleDelete,
  setOperatorModelEnabled,
  setOrgAutofile,
  setOrgTwoStep,
} from "@/lib/api";
import type { OperatorModel, OrgAdmin, Organisation } from "@/lib/types";
import type { Confirm } from "./ConfirmDialog";
import { DISABLE_EFFECT, deleteEffect } from "./logic";

export type OrgChange = "disable" | "enable" | "delete" | "cancel";

/** The model's name in the Models table, where the focus goes after a change. */
export const modelFocusId = (id: string) => `model-name-${id.replace(/[^A-Za-z0-9_-]/g, "-")}`;

/** Enable or disable a model on the platform (MC2): both need a fresh code, as the server. */
export function modelConfirm(model: OperatorModel, enable: boolean): Confirm {
  const name = model.name;
  return {
    title: `${enable ? "Enable" : "Disable"} ${name}?`,
    effect: enable
      ? "Organisations can then be allowed to read scans with it. Their pages go only to an approved " +
        "provider, with zero data retention."
      :"No organisation can be given it any more. It must first be removed from every organisation.",
    action: enable ? "Enable model" : "Disable model",
    done: `${enable ? "Enabled" : "Disabled"} ${name}.`,
    danger: !enable,
    stepUp: true,
    run: (code) => setOperatorModelEnabled(model.id, enable, code),
    focusId: modelFocusId(model.id),
  };
}

/** The organisation's name in its row, where the focus goes after a change. */
export const orgFocusId = (id: string) => `org-name-${id}`;

export function orgConfirm(org: Organisation, change: OrgChange, now: number): Confirm {
  const name = org.name;
  const focusId = orgFocusId(org.id);
  switch (change) {
    case "disable":
      return {
        title: `Disable ${name}?`,
        effect: DISABLE_EFFECT,
        action: "Disable organisation",
        done: `Disabled ${name}.`,
        danger: true,
        stepUp: true,
        run: (code) => disableOrg(org.id, code),
        focusId,
      };
    case "enable":
      return {
        title: `Enable ${name}?`,
        effect: "Its users can sign in again. Their old sessions stay ended.",
        action: "Enable organisation",
        done: `Enabled ${name}.`,
        danger: false,
        stepUp: false,
        run: () => enableOrg(org.id),
        focusId,
      };
    case "delete":
      return {
        title: `Delete ${name}?`,
        effect: deleteEffect(now),
        action: "Delete organisation",
        done: `${name} will be deleted. You can cancel until then.`,
        danger: true,
        stepUp: true,
        typeName: name,
        run: (code, typed) => scheduleDelete(org.id, typed, code),
        focusId,
      };
    case "cancel":
      return {
        title: `Cancel deleting ${name}?`,
        effect: "Nothing is deleted. It stays disabled: enable it when its users should come back.",
        action: "Cancel deletion",
        done: `${name} will not be deleted. It is disabled.`,
        danger: false,
        stepUp: true,
        run: (code) => cancelDelete(org.id, code),
        focusId,
      };
  }
}

/** Allow or stop auto-filing for one organisation (feature 28): no fresh code, as its models. */
export function autofileConfirm(org: Organisation, allow: boolean): Confirm {
  const name = org.name;
  return {
    title: allow ? `Let ${name} file new uploads into folders by type?` : `Stop ${name} filing uploads by type?`,
    effect: allow
      ? "Its admin can then turn auto-filing on in Settings. Files already uploaded stay where they are."
      : "Its switch turns off too. Files already filed stay where they are.",
    action: allow ? "Allow auto-filing" : "Stop auto-filing",
    done: allow ? `${name} can use auto-filing.` : `Auto-filing stopped for ${name}.`,
    danger: !allow,
    stepUp: false,
    run: () => setOrgAutofile(org.id, allow),
    focusId: orgFocusId(org.id),
  };
}

/** Allow or stop two-step sign-in for one organisation's admins: no fresh code, as auto-filing. */
export function twoStepConfirm(org: Organisation, allow: boolean): Confirm {
  const name = org.name;
  return {
    title: allow ? `Let ${name}'s admins use two-step sign-in?` : `Stop two-step sign-in for ${name}?`,
    effect: allow
      ? "Its admins can then turn on a 6-digit code from an authenticator app in Settings. Admins who turned it on before are asked for their code again."
      : "Its admins then sign in with their e-mail and password only. Codes already set up are kept for later.",
    action: allow ? "Allow two-step sign-in" : "Stop two-step sign-in",
    done: allow ? `${name} can use two-step sign-in.` : `Two-step sign-in stopped for ${name}.`,
    danger: !allow,
    stepUp: false,
    run: () => setOrgTwoStep(org.id, allow),
    focusId: orgFocusId(org.id),
  };
}

/** The admin's name in the admins dialog, where the focus goes after a change. */
export const adminFocusId = (id: string) => `admin-name-${id}`;

export type AdminAction = "disable" | "enable" | "unlock" | "reset-2fa";

export function adminConfirm(orgId: string, admin: OrgAdmin, change: AdminAction): Confirm {
  const name = admin.name;
  const run = (code: string) => changeAdmin(orgId, admin.id, change, code);
  const focusId = adminFocusId(admin.id);
  switch (change) {
    case "disable":
      return {
        title: `Disable ${name}?`,
        effect: "They are signed out now and cannot sign in until you enable them again.",
        action: "Disable admin",
        done: `Disabled ${name}.`,
        danger: true,
        stepUp: false,
        run,
        focusId,
      };
    case "enable":
      return {
        title: `Enable ${name}?`,
        effect: "They can sign in again.",
        action: "Enable admin",
        done: `Enabled ${name}.`,
        danger: false,
        stepUp: false,
        run,
        focusId,
      };
    case "unlock":
      return {
        title: `Unlock ${name}?`,
        effect: "They can try to sign in again now, without waiting for the lock to end.",
        action: "Unlock admin",
        done: `Unlocked ${name}.`,
        danger: false,
        stepUp: false,
        run,
        focusId,
      };
    case "reset-2fa":
      return {
        title: `Reset two-step for ${name}?`,
        effect:
          "Their two-step sign-in is turned off and they are signed out. They sign in with their password " +
          "alone, then can turn two-step on again in Admin, Settings.",
        action: "Reset two-step",
        done: `Two-step is off for ${name}.`,
        danger: true,
        stepUp: true,
        run,
        focusId,
      };
  }
}
