import { describe, expect, it } from "vitest";
import {
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
