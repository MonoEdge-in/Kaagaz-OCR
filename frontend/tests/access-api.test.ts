// PRD 7 PR 11a (#604): the access calls send what PR 10a and 10b's routes take: the password only
// when one was typed, and a membership without `head` keeps the stored tick.

import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

describe("the access calls", () => {
  it("send each route its method, path and body", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push(`${init.method} ${url} ${init.body ?? ""}`.trim());
      return new Response("{}", { status: 200 });
    });
    await api.getPersonAccess("u1");
    await api.setManagement("u1", true, "secret");
    await api.setManagement("u1", false);
    await api.putMembership("u1", "d1");
    await api.putMembership("u1", "d1", { head: true, password: "secret" });
    await api.removeMembership("u1", "d1");
    await api.listAdminDepartments();
    await api.listDepartmentMembers("d1");
    await api.departmentOnlyPreview("d1", false);
    await api.setDepartmentOnly("d1", true);
    await api.addNamed("f1", "u1");
    await api.removeNamed("f1", "u1");
    await api.giveFile("doc1", "u1", "secret");
    await api.takeFile("doc1", "u1");
    expect(calls).toEqual([
      "GET /api/admin/users/u1/access",
      'PUT /api/admin/users/u1/management {"on":true,"password":"secret"}',
      'PUT /api/admin/users/u1/management {"on":false}',
      "PUT /api/admin/users/u1/departments/d1 {}",
      'PUT /api/admin/users/u1/departments/d1 {"head":true,"password":"secret"}',
      "DELETE /api/admin/users/u1/departments/d1",
      "GET /api/admin/departments",
      "GET /api/admin/departments/d1/members",
      "GET /api/admin/departments/d1/department-only/preview?on=false",
      'PUT /api/admin/departments/d1/department-only {"on":true}',
      "PUT /api/folders/f1/named/u1 {}",
      "DELETE /api/folders/f1/named/u1",
      'PUT /api/documents/doc1/shares/u1 {"password":"secret"}',
      "DELETE /api/documents/doc1/shares/u1",
    ]);
  });
});
