// PRD 7 PR 11 (#604): who may use which access control, the role table in section 5 of the plan.

import { describe, expect, it } from "vitest";
import { accessAbilities } from "@/components/access/abilities";
import type { Me, Role } from "@/lib/types";

const FINANCE = "d-finance";
const SALES = "d-sales";

function me(role: Role, scope: "app" | "admin", access?: Me["access"]): Me {
  return {
    user: { id: "u1", role } as Me["user"],
    customer: { id: "c1", name: "Sharma Traders" },
    session: { scope },
    access,
  };
}

const all = { audience: true, makePrivate: true, editNamed: true, giveFile: true, keptStrip: true };
const none = { audience: false, makePrivate: false, editNamed: false, giveFile: false, keptStrip: false };

describe("accessAbilities", () => {
  it("gives an admin session everything, anywhere", () => {
    expect(accessAbilities(me("admin", "admin"), { departmentId: FINANCE })).toEqual(all);
    expect(accessAbilities(me("admin", "admin"), { departmentId: null })).toEqual(all);
  });

  it("gives the Head of the place's department who can see it and the named list, nothing more", () => {
    const head = me("member", "app", { management: false, heads: [FINANCE] });
    expect(accessAbilities(head, { departmentId: FINANCE })).toEqual({ ...none, audience: true, editNamed: true });
    expect(accessAbilities(head, { departmentId: SALES })).toEqual(none); // another department
    expect(accessAbilities(head, { departmentId: null })).toEqual(none);
  });

  it("gives an admin with an app session nothing, even as a Head", () => {
    expect(accessAbilities(me("admin", "app", { management: false, heads: [FINANCE] }), { departmentId: FINANCE })).toEqual(none);
  });

  it("gives Management, members, reviewers and an older server's reply nothing", () => {
    expect(accessAbilities(me("member", "app", { management: true, heads: [] }), { departmentId: FINANCE })).toEqual(none);
    expect(accessAbilities(me("reviewer", "app", { management: false, heads: [] }), { departmentId: FINANCE })).toEqual(none);
    expect(accessAbilities(me("member", "app"), { departmentId: FINANCE })).toEqual(none); // no me.access
  });
});
