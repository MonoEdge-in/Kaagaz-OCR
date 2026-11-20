// Who may use which access control (PRD 7 PR 11, #604, section 5 of the plan). Hiding a control is
// only a convenience: the server checks every rule again.

import { adminGate } from "@/components/admin/logic";
import type { Me } from "@/lib/types";
import type { FolderPlace } from "./places";

export interface AccessAbilities {
  audience: boolean; // "Who can see this"
  makePrivate: boolean; // Make private, Make open again
  editNamed: boolean; // a private folder's named list
  giveFile: boolean; // Give access to a file, one or many
  keptStrip: boolean; // "5 files here are kept from you"
}

const NONE: AccessAbilities = { audience: false, makePrivate: false, editNamed: false, giveFile: false, keptStrip: false };

/** An admin session: everything. The Head of the place's department (`departmentId`: the root
 * department of a folder, or a file's department): who can see it and the named list. Anyone else,
 * an admin with an app session, or an older server without `me.access`: nothing. */
export function accessAbilities(me: Me, place: { departmentId: string | null }): AccessAbilities {
  if (adminGate(me) === "allow") return { audience: true, makePrivate: true, editNamed: true, giveFile: true, keptStrip: true };
  if (me.user.role === "admin") return NONE;
  const head = place.departmentId !== null && (me.access?.heads ?? []).includes(place.departmentId);
  return head ? { ...NONE, audience: true, editNamed: true } : NONE;
}

/** #700: the folder's "Who can see this" reads Make private, and opens the panel at that step, for
 * who may make it private on a folder that can be made private now (not private yet, nothing
 * refusing it, places.ts privateRefusal); else it stays "Who can see this". */
export const privateFirst = (abilities: AccessAbilities, place: FolderPlace | null) =>
  abilities.makePrivate && place !== null && !place.isPrivate && place.refusal === null;
