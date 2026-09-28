import { describe, expect, it } from "vitest";
import {
  TABS,
  describeAction,
  describeDetails,
  firstInvalidField,
  parseUploadLimit,
  statTiles,
  tabAfterKey,
  validateNewUser,
} from "@/components/admin/logic";
import type { Stats } from "@/lib/types";

describe("tabAfterKey", () => {
  const count = TABS.length;

  it("moves with the arrow keys and wraps round", () => {
    expect(tabAfterKey(0, "ArrowRight", count)).toBe(1);
    expect(tabAfterKey(count - 1, "ArrowRight", count)).toBe(0);
    expect(tabAfterKey(0, "ArrowLeft", count)).toBe(count - 1);
  });

  it("jumps with Home and End, and ignores other keys", () => {
    expect(tabAfterKey(2, "Home", count)).toBe(0);
    expect(tabAfterKey(0, "End", count)).toBe(count - 1);
    expect(tabAfterKey(1, "Enter", count)).toBeNull();
  });
});

describe("validateNewUser", () => {
  const good = {
    email: "ravi@sharma.in",
    display_name: "Ravi",
    role: "member" as const,
    password: "twelve chars",
  };

  it("accepts a complete user", () => {
    expect(validateNewUser(good)).toEqual({});
  });

  it("names each problem against its field", () => {
    expect(validateNewUser({ ...good, email: "ravi", display_name: "  ", password: "short" })).toEqual({
      email: "Enter a valid e-mail address.",
      display_name: "Enter a name.",
      password: "Use at least 12 characters.",
    });
  });

  it("needs a password of at least 12 characters", () => {
    expect(validateNewUser({ ...good, password: "elevenchars" }).password).toBeDefined();
    expect(validateNewUser({ ...good, password: "twelvechars!" }).password).toBeUndefined();
  });
});

describe("firstInvalidField", () => {
  it("picks the first problem in screen order, for focus", () => {
    expect(firstInvalidField({ password: "x", email: "y" })).toBe("email");
    expect(firstInvalidField({ password: "x", display_name: "y" })).toBe("display_name");
    expect(firstInvalidField({ password: "x" })).toBe("password");
    expect(firstInvalidField({})).toBeNull();
  });
});

describe("parseUploadLimit", () => {
  it("treats an empty box as the platform limit", () => {
    expect(parseUploadLimit("  ", 50)).toEqual({ ok: true, value: null });
  });

  it("accepts whole numbers from 1 to the platform limit", () => {
    expect(parseUploadLimit("1", 50)).toEqual({ ok: true, value: 1 });
    expect(parseUploadLimit(" 50 ", 50)).toEqual({ ok: true, value: 50 });
  });

  it("refuses anything else", () => {
    expect(parseUploadLimit("0", 50)).toEqual({
      ok: false,
      message: "The limit must be at least 1 MB.",
    });
    expect(parseUploadLimit("51", 50)).toEqual({
      ok: false,
      message: "The limit can be at most 50 MB.",
    });
    expect(parseUploadLimit("2.5", 50).ok).toBe(false);
    expect(parseUploadLimit("-3", 50).ok).toBe(false);
    expect(parseUploadLimit("ten", 50).ok).toBe(false);
  });
});

describe("statTiles", () => {
  const stats: Stats = {
    documents_by_status: { ready: 120000, failed: 2 },
    questions_total: 40,
    answered: 30,
    not_found: 10,
    verified_answers: 25,
    needs_review_answers: 5,
    feedback_up: 7,
    feedback_down: 1,
    open_review_items: 3,
  };

  it("adds up documents and fills missing statuses with zero", () => {
    const tiles = statTiles(stats);
    expect(tiles[0]).toEqual({ label: "Documents", value: "1,20,002" });
    expect(tiles.find((tile) => tile.label === "Documents being read")?.value).toBe("0");
  });

  it("says Verified and Needs review in words", () => {
    const labels = statTiles(stats).map((tile) => tile.label);
    expect(labels).toContain("Answers labelled Verified");
    expect(labels).toContain("Answers labelled Needs review");
  });
});

describe("describeDetails", () => {
  it("lists keys in order as plain text", () => {
    expect(describeDetails({ status: "ready", count: 3, ok: true, parent: null })).toBe(
      "count: 3, ok: true, parent: none, status: ready",
    );
  });

  it("shows nested values as JSON text, never markup", () => {
    expect(describeDetails({ ids: ["a", "b"] })).toBe('ids: ["a","b"]');
    expect(describeDetails({ status: "<b>x</b>" })).toBe("status: <b>x</b>");
  });

  it("is empty for no details", () => {
    expect(describeDetails({})).toBe("");
  });
});

describe("describeAction", () => {
  it("puts known actions in words and leaves unknown ones as they are", () => {
    expect(describeAction("document.upload")).toBe("Uploaded a document");
    expect(describeAction("something.new")).toBe("something.new");
  });
});
