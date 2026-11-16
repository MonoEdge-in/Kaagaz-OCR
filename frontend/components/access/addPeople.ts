// Adding several people to a department (PRD 7 PR 11a, #604 review): me first, so my password is
// asked before anyone is added, and Cancel there stops the whole add; then each of the others, one
// call each, a failure noted and the rest still added. Tested in tests/access-add-people.test.ts.

import type { Person } from "@/lib/types";

export interface AddedPeople {
  added: number; // newly in it (an existing member changes nothing)
  failed: { person: Person; reason: string }[];
  cancelled: boolean; // my password step was cancelled: nobody was added
}

/** `add(person, first)`: sends one add (through the password step, asked first when `first`);
 * null when that step was cancelled. `reason`: a failure in words. */
export async function addPeople(
  people: readonly Person[],
  myId: string,
  add: (person: Person, first: boolean) => Promise<{ changed: boolean } | null>,
  reason: (caught: unknown) => string,
): Promise<AddedPeople> {
  const ordered = [...people.filter((p) => p.id === myId), ...people.filter((p) => p.id !== myId)];
  const result: AddedPeople = { added: 0, failed: [], cancelled: false };
  for (const person of ordered) {
    try {
      const reply = await add(person, person.id === myId);
      if (reply === null) return { ...result, cancelled: true };
      if (reply.changed) result.added += 1;
    } catch (caught) {
      result.failed.push({ person, reason: reason(caught) });
    }
  }
  return result;
}
