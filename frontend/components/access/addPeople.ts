// Adding several people to a department (PRD 7 PR 11a, #604 review): me first, so a company
// sign-in check comes before anyone is added, and stops the whole add; then each of the others, one
// call each, a failure noted and the rest still added. Tested in tests/access-add-people.test.ts.

import type { Person } from "@/lib/types";

export interface AddedPeople {
  added: number; // newly in it (an existing member changes nothing)
  failed: { person: Person; reason: string }[];
  cancelled: boolean; // the company sign-in was started for my own add: nobody was added
}

/** `add(person)`: sends one add (through the company sign-in step); null when that step started
 * the sign-in instead. `reason`: a failure in words. */
export async function addPeople(
  people: readonly Person[],
  myId: string,
  add: (person: Person) => Promise<{ changed: boolean } | null>,
  reason: (caught: unknown) => string,
): Promise<AddedPeople> {
  const ordered = [...people.filter((p) => p.id === myId), ...people.filter((p) => p.id !== myId)];
  const result: AddedPeople = { added: 0, failed: [], cancelled: false };
  for (const person of ordered) {
    try {
      const reply = await add(person);
      if (reply === null) return { ...result, cancelled: true };
      if (reply.changed) result.added += 1;
    } catch (caught) {
      result.failed.push({ person, reason: reason(caught) });
    }
  }
  return result;
}
