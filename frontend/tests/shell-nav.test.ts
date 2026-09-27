import { describe, expect, it } from "vitest";
import { isCurrent, navLinks, roleName } from "@/components/shell/nav";

describe("navLinks", () => {
  const labels = (role: Parameters<typeof navLinks>[0]) => navLinks(role).map((l) => l.label);

  it("gives a member Documents and Ask only", () => {
    expect(labels("member")).toEqual(["Documents", "Ask"]);
  });

  it("adds Review for a reviewer", () => {
    expect(labels("reviewer")).toEqual(["Documents", "Ask", "Review"]);
  });

  it("adds Review and Admin for an admin", () => {
    expect(labels("admin")).toEqual(["Documents", "Ask", "Review", "Admin"]);
  });

  it("points Ask at /chat", () => {
    expect(navLinks("member").find((l) => l.label === "Ask")?.href).toBe("/chat");
  });
});

describe("isCurrent", () => {
  it("matches the path and paths below it, not look-alikes", () => {
    expect(isCurrent("/documents", "/documents")).toBe(true);
    expect(isCurrent("/documents", "/documents/abc")).toBe(true);
    expect(isCurrent("/documents", "/documentsx")).toBe(false);
    expect(isCurrent("/admin", "/documents")).toBe(false);
  });
});

describe("roleName", () => {
  it("names each role", () => {
    expect(roleName("member")).toBe("Member");
    expect(roleName("reviewer")).toBe("Reviewer");
    expect(roleName("admin")).toBe("Admin");
  });
});
