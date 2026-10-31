// #468 B5: admin's unsent work is kept in this tab for the same admin (lib/drafts.ts): Add a
// user (email, name and role, never the temporary password), and Settings' and AI settings'
// unsaved changes. Switching admin tabs with unsaved changes asks first.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ADD_USER_DRAFT,
  LEAVE_UNSAVED,
  SETTINGS_DRAFT,
  aiChangesToKeep,
  aiSettingsDraft,
  mineDraftOver,
  newUserDraft,
  newUserFromDraft,
  orgDraftOver,
  settingsDraft,
  settingsFromDraft,
  tabChangeAllowed,
} from "@/components/admin/logic";
import { clearDrafts, readDraft, saveDraft } from "@/lib/drafts";
import type { AiSettings, AiTaskSetting, CustomerSettings } from "@/lib/types";

const ADMIN = "11111111-1111-4111-8111-111111111111";
const OTHER = "22222222-2222-4222-8222-222222222222";
const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");

/** A tab's sessionStorage, in memory. */
class MemoryStorage {
  items = new Map<string, string>();
  get length() {
    return this.items.size;
  }
  key(index: number) {
    return [...this.items.keys()][index] ?? null;
  }
  getItem(key: string) {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.items.set(key, String(value));
  }
  removeItem(key: string) {
    this.items.delete(key);
  }
}

let storage: MemoryStorage;
beforeEach(() => {
  storage = new MemoryStorage();
  vi.stubGlobal("sessionStorage", storage);
});
afterEach(() => {
  clearDrafts();
  vi.unstubAllGlobals();
});

describe("Add a user: email, name and role, never the password", () => {
  const typed = { email: "asha@example.com", display_name: "Asha", role: "reviewer" as const, password: "Temp-password-123" };

  it("keeps the three fields and never the temporary password", () => {
    expect(newUserDraft(typed)).toEqual({ email: "asha@example.com", display_name: "Asha", role: "reviewer" });
    saveDraft(ADMIN, ADD_USER_DRAFT, newUserDraft(typed));
    expect([...storage.items.values()].join("")).not.toContain("Temp-password-123");
    expect([...storage.items.values()].join("")).not.toContain("password");
  });

  it("keeps nothing until an email or a name is typed (a password alone is not kept)", () => {
    expect(newUserDraft({ ...typed, email: "", display_name: "" })).toBeNull();
  });

  it("comes back for the same admin, with an empty password; another admin never sees it", () => {
    saveDraft(ADMIN, ADD_USER_DRAFT, newUserDraft(typed));
    expect(newUserFromDraft(readDraft(ADMIN, ADD_USER_DRAFT))).toEqual({ ...typed, password: "" });
    expect(readDraft(OTHER, ADD_USER_DRAFT)).toBeNull();
    // Changed on purpose (#468 review, PT5): reading no longer drops other people's drafts; the
    // session does that once who is signed in is known (keepDraftsOf).
  });

  it("an odd kept value falls back to the empty form's", () => {
    expect(newUserFromDraft({ email: 3, display_name: null, role: "owner" })).toEqual({
      email: "",
      display_name: "",
      role: "member",
      password: "",
    });
    expect(newUserFromDraft(null).role).toBe("member");
  });

  it("the panel restores on open, saves as it is typed, clears when added or closed", () => {
    const panel = read("components/admin/UsersPanel.tsx");
    // Changed on purpose (#468 review, UX4): the kept draft is read once, then fills the form.
    expect(panel).toContain("const [kept] = useState(() => readDraft(userId, ADD_USER_DRAFT));");
    expect(panel).toContain("useState<NewUserInput>(() => newUserFromDraft(kept))");
    expect(panel).toMatch(/saveDraftSoon\(userId, ADD_USER_DRAFT, newUserDraft\(input\)\)/);
    expect(panel.match(/clearDraft\(userId, ADD_USER_DRAFT\)/g)?.length).toBeGreaterThanOrEqual(2);
  });
});

describe("Settings: the unsaved upload limit and auto-filing", () => {
  const saved: CustomerSettings = {
    customer_name: "Acme",
    max_upload_mb: 20,
    platform_max_upload_mb: 50,
    autofile: { granted: true, on: false },
  };

  // Changed on purpose (#468 review, CR1): only the fields that differ, each on its own.
  it("keeps only what differs from what is saved", () => {
    expect(settingsDraft(saved, "30", true)).toEqual({ uploadLimit: "30", autofile: true });
    expect(settingsDraft(saved, "30", false)).toEqual({ uploadLimit: "30" });
    expect(settingsDraft(saved, "20", false)).toBeNull();
  });

  it("puts a kept draft back over what is saved; auto-filing only while it is allowed", () => {
    expect(settingsFromDraft({ uploadLimit: "30", autofile: true }, saved)).toEqual({ text: "30", autofileOn: true });
    expect(settingsFromDraft(null, saved)).toEqual({ text: "20", autofileOn: false });
    expect(settingsFromDraft({ uploadLimit: "30", autofile: true }, { ...saved, autofile: { granted: false, on: false } })).toEqual({
      text: "30",
      autofileOn: false,
    });
    expect(settingsFromDraft({ uploadLimit: 7 }, saved).text).toBe("20"); // not text: ignored
  });

  it("the panel reads the draft for the same admin when the settings arrive, and saves while changed", () => {
    expect(SETTINGS_DRAFT).not.toBe(ADD_USER_DRAFT);
    const panel = read("components/admin/SettingsPanel.tsx");
    expect(panel).toContain("settingsFromDraft(readDraft(userId, SETTINGS_DRAFT), result)");
    expect(panel).toMatch(/if \(!settings\) return;[^\n]*\n\s*saveDraftSoon\(userId, SETTINGS_DRAFT, settingsDraft\(settings, text, autofileOn\)\)/);
    expect(panel).toContain("clearDraft(userId, SETTINGS_DRAFT)");
  });
});

describe("AI settings: the unsaved levels", () => {
  const task = (more: Partial<AiTaskSetting>): AiTaskSetting => ({
    task: "ask",
    effective: "low",
    mine: null,
    organisation: "low",
    locked: false,
    allowed: ["low", "high", "max"],
    available: true,
    ...more,
  });
  const settings: AiSettings = {
    tasks: [task({}), task({ task: "ensemble", allowed: ["low"], organisation: "low", effective: "low" })],
    can_edit_organisation: true,
  };

  // Changed on purpose (#468 review, CR1): given what is saved, an organisation's task keeps only its changed part.
  it("keeps the changed tasks only, as plain values", () => {
    const before = { ask: { level: "low" as const, locked: false } };
    expect(aiChangesToKeep(before, { ask: { level: "high", locked: true } })).toEqual({ ask: { level: "high", locked: true } });
    expect(aiChangesToKeep({ ask: "low" }, { ask: null, ensemble: "low" })).toEqual({ ask: null, ensemble: "low" });
    expect(aiChangesToKeep(before, {})).toBeNull();
  });

  it("an organisation's draft comes back only for tasks there, with a level allowed now", () => {
    const saved = { ask: { level: "low" as const, locked: false }, ensemble: { level: "low" as const, locked: false } };
    expect(orgDraftOver(saved, { ask: { level: "high", locked: true } }, settings)).toEqual({
      ...saved,
      ask: { level: "high", locked: true },
    });
    // Not allowed any more, not a task, or the wrong shape: what is saved stays.
    expect(orgDraftOver(saved, { ensemble: { level: "high", locked: true }, scan: { level: "low", locked: false }, ask: "high" }, settings)).toEqual(saved);
    expect(orgDraftOver(saved, null, settings)).toEqual(saved);
  });

  it("a member's draft comes back for tasks not locked, with an allowed level or the organisation's (null)", () => {
    const saved = { ask: null, ensemble: null };
    expect(mineDraftOver(saved, { ask: "high" }, settings)).toEqual({ ask: "high", ensemble: null });
    expect(mineDraftOver({ ask: "low", ensemble: null }, { ask: null }, settings)).toEqual({ ask: null, ensemble: null });
    const locked = { ...settings, tasks: [task({ locked: true })] };
    expect(mineDraftOver({ ask: null }, { ask: "high" }, locked)).toEqual({ ask: null });
    expect(mineDraftOver(saved, { ask: "huge" }, settings)).toEqual(saved);
  });

  it("each scope has its own draft", () => {
    expect(aiSettingsDraft("organisation")).not.toBe(aiSettingsDraft("member"));
    const panel = read("components/admin/AiSettingsPanel.tsx");
    expect(panel).toContain("readDraft(userId, aiSettingsDraft(scope))");
    expect(panel).toMatch(/saveDraftSoon\(userId, aiSettingsDraft\(scope\), aiChangesToKeep\(draftOf\(scope, settings\), changes\)\)/);
    expect(panel).toContain("clearDraft(userId, aiSettingsDraft(scope))");
  });
});

describe("switching admin tabs with unsaved changes asks first", () => {
  it("says the issue's words, and asks only when something is unsaved", () => {
    expect(LEAVE_UNSAVED).toBe("You have unsaved changes. Leave without saving?");
    const ask = vi.fn(() => false);
    expect(tabChangeAllowed(false, ask)).toBe(true);
    expect(ask).not.toHaveBeenCalled();
    expect(tabChangeAllowed(true, ask)).toBe(false);
    expect(tabChangeAllowed(true, () => true)).toBe(true);
  });

  it("AdminView asks before leaving Settings or AI settings, and drops their draft on leaving", () => {
    const view = read("components/admin/AdminView.tsx");
    expect(view).toContain("tabChangeAllowed(unsaved, () => window.confirm(LEAVE_UNSAVED))");
    expect(view).toContain("<SettingsPanel providers={me.providers} onUnsaved={setUnsaved} />");
    expect(view).toContain('<AiSettingsPanel scope="organisation" onUnsaved={setUnsaved} />');
    // Changed on purpose (#468 review, SF3): only the draft of the tab being left.
    expect(view).toContain('if (active === "settings") clearDraft(me.user.id, SETTINGS_DRAFT);');
    expect(view).toContain('if (active === "ai") clearDraft(me.user.id, aiSettingsDraft("organisation"));');
  });
});
