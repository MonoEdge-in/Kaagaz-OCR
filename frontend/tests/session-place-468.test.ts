import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { endedPath, pathAfterSignIn, safeNextPath } from "@/components/shell/gate";

// Issue 468 (A1): a session that ends keeps your place. The sign-in comes back to the screen
// it was on, and only ever to a path on this site.

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

describe("the place a sign-in comes back to (safeNextPath)", () => {
  it("keeps a plain path on this site, with its query", () => {
    for (const path of [
      "/",
      "/dashboard",
      "/documents/0b6c2f0e-2a35-4d55-9a57-6f4b8f6b2d11",
      "/chat?c=0b6c2f0e-2a35-4d55-9a57-6f4b8f6b2d11",
      "/search?q=total%20amount&tab=names",
      "/search?q=50%25",
      "/admin",
      "/admin?tab=settings",
      "/documents/d-1#page-2",
    ]) {
      expect(safeNextPath(path), path).toBe(path);
    }
  });

  it("refuses another site, a scheme, and their encoded forms (no open redirect)", () => {
    for (const path of [
      "//evil.com",
      "//evil.com/path",
      "/\\evil.com",
      "\\\\evil.com",
      "https://evil.com",
      "https:evil.com",
      "http://evil.com",
      "javascript:alert(1)",
      "/javascript:alert(1)",
      "JavaScript:alert(1)",
      "data:text/html,x",
      "%2F%2Fevil.com",
      "/%2F%2Fevil.com",
      "/%2f%2fevil.com",
      "/%5Cevil.com",
      "/%5cevil.com",
      "/%252F%252Fevil.com",
      "/%25252F%25252Fevil.com",
      "/%2525252F%2525252Fevil.com",
      "/x%3Aevil",
      "/%E0%A4%A", // broken encoding
      "evil.com",
      "",
    ]) {
      expect(safeNextPath(path), path).toBeNull();
    }
  });

  it("refuses control characters, which a browser drops (/<tab>/evil.com is //evil.com)", () => {
    for (const path of ["/\t/evil.com", "/\n/evil.com", "/\r/evil.com", "/x\u0000", "/x\u007f", "/%09/evil.com", "/%0A/evil.com"]) {
      expect(safeNextPath(path), JSON.stringify(path)).toBeNull();
    }
  });

  it("refuses anything but one string, and very long addresses", () => {
    expect(safeNextPath(undefined)).toBeNull();
    expect(safeNextPath(["/documents", "/chat"])).toBeNull();
    expect(safeNextPath(7)).toBeNull();
    expect(safeNextPath(`/${"a".repeat(2000)}`)).toBeNull();
  });
});

describe("where a sign-in goes (pathAfterSignIn)", () => {
  // Changed on purpose (issue 468): any safe path, not only /admin or the dashboard.
  it("goes back to the safe ?next= path, else the dashboard", () => {
    expect(pathAfterSignIn({ next: "/chat?c=0b6c2f0e-2a35-4d55-9a57-6f4b8f6b2d11" })).toBe(
      "/chat?c=0b6c2f0e-2a35-4d55-9a57-6f4b8f6b2d11",
    );
    expect(pathAfterSignIn({ next: "/admin" })).toBe("/admin");
    expect(pathAfterSignIn({})).toBe("/dashboard");
    for (const next of ["//evil.com", "/\\evil.com", "https://evil.com", "%2F%2Fevil.com", "javascript:alert(1)", "/%2F%2Fevil.com"]) {
      expect(pathAfterSignIn({ next }), next).toBe("/dashboard");
    }
    expect(pathAfterSignIn({ next: ["/a", "/b"] })).toBe("/dashboard");
  });
});

describe("the sign-in page after a session ends (endedPath)", () => {
  it("carries the screen's path and query in ?next=, encoded, with the note", () => {
    expect(endedPath("/documents/d-1")).toBe("/login?next=%2Fdocuments%2Fd-1&ended=1");
    expect(endedPath("/chat?c=abc&x=1")).toBe("/login?next=%2Fchat%3Fc%3Dabc%26x%3D1&ended=1");
    expect(endedPath("/admin")).toBe("/login?next=%2Fadmin&ended=1");
    // Read back the way the sign-in page reads it: the same place.
    const next = new URLSearchParams(endedPath("/search?q=a%26b&tab=names").split("?")[1]).get("next");
    expect(pathAfterSignIn({ next: next ?? undefined })).toBe("/search?q=a%26b&tab=names");
  });

  it("falls back to the plain sign-in when the place is not a safe path", () => {
    expect(endedPath("//evil.com")).toBe("/login?ended=1");
    expect(endedPath("/x:y")).toBe("/login?ended=1");
  });

  it("is given the address bar's path and query, and drafts are written just before leaving", () => {
    const session = read("lib/session.tsx");
    expect(session).toMatch(/flushDrafts\(\);\s*leavePage\(endedPath\(window\.location\.pathname \+ window\.location\.search\)\)/);
  });
});

describe("drafts and who is signed in (lib/session.tsx, the sign-outs)", () => {
  it("drops other people's drafts once the signed-in person is known", () => {
    expect(read("lib/session.tsx")).toMatch(/if \(signedInId !== null\) keepDraftsOf\(signedInId\)/);
  });

  it("clears drafts when another tab signs out or someone else signs in", () => {
    expect(read("lib/session.tsx")).toMatch(/if \(to === null\) return;\s*clearDrafts\(\);[^\n]*\s*leavePage\(to\);/);
  });

  it("clears drafts on every sign-out", () => {
    for (const file of ["components/shell/AccountMenu.tsx", "components/shell/SetPasswordScreen.tsx"]) {
      const text = read(file);
      expect(text.indexOf("clearDrafts();"), file).toBeGreaterThan(0);
      expect(text.indexOf("clearDrafts();"), file).toBeLessThan(text.indexOf("leavePage(signedOutPath(pathname))"));
    }
  });
});
