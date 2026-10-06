// What each change on the operator screens says and does, for ConfirmDialog.
// Taking something away (disable an organisation, delete it, cancel its
// deletion) needs a fresh code (step-up), as the server does; enabling does not.

import { cancelDelete, disableOrg, enableOrg, scheduleDelete } from "@/lib/api";
import type { Organisation } from "@/lib/types";
import type { Confirm } from "./ConfirmDialog";
import { DISABLE_EFFECT, deleteEffect } from "./logic";

export type OrgChange = "disable" | "enable" | "delete" | "cancel";

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
