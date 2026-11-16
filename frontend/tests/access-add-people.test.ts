// PRD 7 PR 11a (#604 review): adding several people to a department. Me first, so my password is
// asked before anyone is added, and Cancel there stops it all; a person who fails is noted and
// the rest are still added; only a real change counts.

import { describe, expect, it } from "vitest";
import { addPeople } from "@/components/access/addPeople";
import { accessErrorText, addedPeopleText } from "@/components/access/words";
import { ApiError } from "@/lib/api";
import { NETWORK_FAILURE } from "@/lib/emailLinks";

const PRIYA = { id: "u1", name: "Priya" };
const RAVI = { id: "u2", name: "Ravi" }; // switched off
const AMIT = { id: "u3", name: "Amit" }; // in Finance already
const NEHA = { id: "u4", name: "Neha" }; // the connection drops
const ME = { id: "me", name: "Asha" };

function fakeAdd(cancelMine = false) {
  const sent: string[] = [];
  const add = async (person: { id: string }, first: boolean) => {
    sent.push(`${person.id}${first ? " (password first)" : ""}`);
    if (person.id === ME.id && cancelMine) return null;
    if (person.id === RAVI.id) throw new ApiError(409, "disabled", "This person is switched off.");
    if (person.id === NEHA.id) throw new TypeError("Failed to fetch");
    return { changed: person.id !== AMIT.id };
  };
  return { sent, add };
}

describe("addPeople", () => {
  it("adds me first with my password, keeps going past failures, and counts only real changes", async () => {
    const api = fakeAdd();
    const result = await addPeople([PRIYA, RAVI, AMIT, NEHA, ME], ME.id, api.add, accessErrorText);
    expect(api.sent).toEqual(["me (password first)", "u1", "u2", "u3", "u4"]);
    expect(result.cancelled).toBe(false);
    expect(result.added).toBe(2); // me and Priya; Amit was in it already
    expect(result.failed.map((each) => each.person.name)).toEqual(["Ravi", "Neha"]);
    expect(addedPeopleText(result.added, "Finance", result.failed)).toBe(
      "Added 2 people to Finance. Couldn't add Ravi: This account is disabled. Enable it before giving access. " +
        `Couldn't add Neha: ${NETWORK_FAILURE.replace(/\.$/, "")}.`,
    );
  });

  it("adds nobody when I cancel my password step", async () => {
    const api = fakeAdd(true);
    const result = await addPeople([PRIYA, ME], ME.id, api.add, accessErrorText);
    expect(api.sent).toEqual(["me (password first)"]);
    expect(result).toEqual({ added: 0, failed: [], cancelled: true });
  });

  it("asks no password when I am not among them", async () => {
    const api = fakeAdd();
    await addPeople([PRIYA, AMIT], ME.id, api.add, accessErrorText);
    expect(api.sent).toEqual(["u1", "u3"]);
  });
});
