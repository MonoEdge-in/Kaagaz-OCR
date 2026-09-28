import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ICON_NAMES } from "@/components/ui/icons";
import { currentPageName, isCurrent, navLinks, roleName } from "@/components/shell/nav";

describe("navLinks", () => {
  const labels = (role: Parameters<typeof navLinks>[0]) => navLinks(role).map((l) => l.label);

  it("gives a member Documents and Ask only", () => {
    expect(labels("member")).toEqual(["Documents", "Ask"]);
  });

  it("gives a member no Review or Admin link, by label or by address", () => {
    const links = navLinks("member");
    expect(links.map((l) => l.href)).toEqual(["/documents", "/chat"]);
    for (const link of links) {
      expect(link.href).not.toMatch(/^\/(review|admin)(\/|$)/);
      expect(link.label).not.toMatch(/review|admin/i);
    }
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

  it("gives every link a known icon", () => {
    for (const link of navLinks("admin")) {
      expect(ICON_NAMES).toContain(link.icon);
    }
  });
});

// The sidebar and the drawer must show the same links. AppShell gets them from
// navLinks() once and renders one ShellMenu element in both places; no shell
// file may hard-code a link to a role-only page beside it.
describe("one nav list for the sidebar and the drawer", () => {
  const dir = join(__dirname, "..", "components", "shell");
  const sources = readdirSync(dir)
    .filter((name) => name.endsWith(".tsx"))
    .map((name) => ({ name, text: readFileSync(join(dir, name), "utf8") }));
  const shell = sources.find((s) => s.name === "AppShell.tsx")?.text ?? "";

  it("calls navLinks() exactly once, in AppShell", () => {
    const calls = sources.flatMap(({ name, text }) =>
      Array.from(text.matchAll(/\bnavLinks\(/g), () => name),
    );
    expect(calls).toEqual(["AppShell.tsx"]);
  });

  it("renders the one ShellMenu element in both the sidebar and the drawer", () => {
    expect(shell.match(/<ShellMenu\b/g)).toHaveLength(1);
    expect(shell.match(/\{menu\}/g)).toHaveLength(2);
    expect(shell).toMatch(/className=\{styles\.sidebar\}[\s\S]*\{menu\}[\s\S]*<Drawer[\s\S]*\{menu\}/);
  });

  it("hard-codes no Review or Admin link in any shell component", () => {
    for (const { name, text } of sources) {
      expect(text, name).not.toMatch(/["'`]\/(review|admin)\b/);
    }
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

describe("currentPageName", () => {
  it("names the page for the top bar, including pages below a link", () => {
    const links = navLinks("admin");
    expect(currentPageName(links, "/documents/abc")).toBe("Documents");
    expect(currentPageName(links, "/chat")).toBe("Ask");
    expect(currentPageName(links, "/admin")).toBe("Admin");
  });

  it("names no page a member has no link for", () => {
    expect(currentPageName(navLinks("member"), "/review")).toBeNull();
    expect(currentPageName(navLinks("member"), "/somewhere")).toBeNull();
  });
});

describe("roleName", () => {
  it("names each role", () => {
    expect(roleName("member")).toBe("Member");
    expect(roleName("reviewer")).toBe("Reviewer");
    expect(roleName("admin")).toBe("Admin");
  });
});
