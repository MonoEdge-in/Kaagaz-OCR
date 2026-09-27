import { describe, expect, it } from "vitest";
import {
  CAPS_LOCK_MESSAGE,
  capsLockFrom,
  caretToKeep,
  describedBy,
  inputType,
  passwordFieldIds,
  remaskOn,
  toggleText,
  visibilityMessage,
} from "@/components/ui/passwordFieldLogic";

describe("masking", () => {
  it("shows dots while masked and the characters while shown", () => {
    expect(inputType(true)).toBe("password");
    expect(inputType(false)).toBe("text");
  });

  it("names the action the button will take, not the state", () => {
    expect(toggleText(true)).toBe("Show");
    expect(toggleText(false)).toBe("Hide");
  });

  it("announces what is on screen after a toggle", () => {
    expect(visibilityMessage(false)).toBe("Password is visible");
    expect(visibilityMessage(true)).toBe("Password is hidden");
  });
});

describe("caretToKeep", () => {
  it("keeps the caret and selection where they were", () => {
    expect(caretToKeep(3, 3, 8)).toEqual({ start: 3, end: 3, direction: "none" });
    expect(caretToKeep(1, 5, 8, "backward")).toEqual({ start: 1, end: 5, direction: "backward" });
  });

  it("falls back to the end when positions are missing or out of range", () => {
    expect(caretToKeep(null, null, 6)).toEqual({ start: 6, end: 6, direction: "none" });
    expect(caretToKeep(-1, 99, 6)).toEqual({ start: 6, end: 6, direction: "none" });
  });

  it("orders a reversed range and ignores an unknown direction", () => {
    expect(caretToKeep(5, 2, 8, "sideways")).toEqual({ start: 2, end: 5, direction: "none" });
  });

  it("works on special characters (the #### case), which are just characters", () => {
    const typed = String.raw`#$%&'"\ #`;
    expect(typed).toHaveLength(9);
    expect(caretToKeep(typed.length, typed.length, typed.length).start).toBe(9);
  });
});

describe("capsLockFrom", () => {
  it("reads Caps Lock from the key event", () => {
    const on = { getModifierState: (key: string) => key === "CapsLock" };
    const off = { getModifierState: () => false };
    expect(capsLockFrom(on)).toBe(true);
    expect(capsLockFrom(off)).toBe(false);
  });

  it("says unknown (null) for events without getModifierState, like autofill", () => {
    expect(capsLockFrom({})).toBeNull();
    expect(capsLockFrom({ getModifierState: "nope" })).toBeNull();
    expect(capsLockFrom({ getModifierState: () => undefined })).toBeNull();
  });

  it("has the message the field shows", () => {
    expect(CAPS_LOCK_MESSAGE).toBe("Caps Lock is on.");
  });
});

describe("remaskOn", () => {
  it("hides the password when the page is left or put in the background", () => {
    expect(remaskOn("pagehide", "visible")).toBe(true);
    expect(remaskOn("visibilitychange", "hidden")).toBe(true);
  });

  it("does not hide it when the tab becomes visible again", () => {
    expect(remaskOn("visibilitychange", "visible")).toBe(false);
    expect(remaskOn("focus", "hidden")).toBe(false);
  });
});

describe("describedBy", () => {
  const ids = passwordFieldIds("pw");

  it("points at the error, then Caps Lock, then the hint", () => {
    expect(describedBy(ids, { error: true, capsLock: true, hint: true })).toBe(
      "pw-error pw-caps pw-hint",
    );
    expect(describedBy(ids, { error: false, capsLock: true, hint: false })).toBe("pw-caps");
  });

  it("gives nothing when there is nothing to describe", () => {
    expect(describedBy(ids, { error: false, capsLock: false, hint: false })).toBeUndefined();
  });
});
