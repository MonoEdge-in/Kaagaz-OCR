import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  closeAllowed,
  closesOnRoute,
  closesOnViewport,
  DESKTOP_QUERY,
  isBackdropClick,
  shouldReturnFocus,
} from "@/components/ui/drawerLogic";

describe("isBackdropClick", () => {
  const dialog = { name: "dialog" };
  const panel = { name: "panel" };

  it("closes on a press and release on the backdrop (the dialog itself)", () => {
    expect(isBackdropClick(dialog, dialog, dialog)).toBe(true);
  });

  it("does not close on clicks inside the panel", () => {
    expect(isBackdropClick(panel, panel, dialog)).toBe(false);
  });

  it("does not close when a drag starts inside and ends on the backdrop", () => {
    expect(isBackdropClick(panel, dialog, dialog)).toBe(false);
  });

  it("does not close with no press recorded or no dialog", () => {
    expect(isBackdropClick(null, dialog, dialog)).toBe(false);
    expect(isBackdropClick(null, null, null)).toBe(false);
  });
});

describe("closesOnRoute", () => {
  it("closes an open drawer when the path changes", () => {
    expect(closesOnRoute("/documents", "/chat", true)).toBe(true);
  });

  it("does nothing on the same path or when closed", () => {
    expect(closesOnRoute("/chat", "/chat", true)).toBe(false);
    expect(closesOnRoute("/documents", "/chat", false)).toBe(false);
  });
});

describe("closesOnViewport", () => {
  it("closes an open drawer at the sidebar width", () => {
    expect(DESKTOP_QUERY).toBe("(min-width: 1024px)");
    expect(closesOnViewport(true, true)).toBe(true);
    expect(closesOnViewport(false, true)).toBe(false);
    expect(closesOnViewport(true, false)).toBe(false);
  });
});

describe("shouldReturnFocus", () => {
  const body = { name: "body" };
  const inside = { name: "inside" };
  const elsewhere = { name: "elsewhere" };
  const dialog = { contains: (node: { name: string } | null) => node === inside };

  it("returns focus when it was lost or is still in the drawer", () => {
    expect(shouldReturnFocus(null, body, dialog)).toBe(true);
    expect(shouldReturnFocus(body, body, dialog)).toBe(true);
    expect(shouldReturnFocus(inside, body, dialog)).toBe(true);
  });

  it("leaves focus alone once the person moved it elsewhere", () => {
    expect(shouldReturnFocus(elsewhere, body, dialog)).toBe(false);
  });
});

// MonoEdge design system (16 Oct 2026, brief B): the backdrop closes only when
// the press and the click were both on it, and never over unsaved changes
// without asking; Esc and the close button ask too.
describe("closing with unsaved changes", () => {
  it("closes at once when nothing is unsaved, without asking", () => {
    let asked = false;
    expect(closeAllowed(false, () => (asked = true))).toBe(true);
    expect(asked).toBe(false);
  });

  it("asks first when something is unsaved, and keeps it open on No", () => {
    expect(closeAllowed(true, () => true)).toBe(true);
    expect(closeAllowed(true, () => false)).toBe(false);
  });

  it("routes Esc, the backdrop and the close button through the question", () => {
    const drawer = readFileSync(join(__dirname, "../components/ui/Drawer.tsx"), "utf8");
    expect(drawer).toMatch(/onPointerDown=\{\(event\) => \{\s*pressTarget\.current = event\.target;/);
    expect(drawer).toContain("if (isBackdropClick(pressTarget.current, event.target, event.currentTarget)) askClose();");
    expect(drawer).toContain("if (open) askClose();");
    expect(drawer).toContain("onClick={askClose} />");
    expect(drawer).toContain("closeAllowed(unsaved, () => window.confirm(UNSAVED_QUESTION))");
  });
});
